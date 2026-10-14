import argparse
import hashlib
import html
import json
import os
import re
import secrets
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from contextlib import closing,contextmanager,nullcontext
from datetime import datetime,timedelta,timezone
from email import policy
from email.parser import BytesParser
from fnmatch import fnmatchcase
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs,urlsplit,urlunsplit
try:import markdown_it;HAS_RENDERER=True
except ImportError:HAS_RENDERER=False
ASSETS=Path(__file__).resolve().parents[1]/'assets'
IDENTIFIER=re.compile('[a-zA-Z0-9_-]{1,80}\\Z')
REPLY_REFERENCE=re.compile('^RE:\\s*([a-zA-Z0-9_-]{1,80})(?:\\s|$)',re.IGNORECASE)
MAX_REPORT=2000000
SNAPSHOT_CAP_BYTES=128000000
MAX_FETCH=SNAPSHOT_CAP_BYTES*80//100
WORKSPACE_SKIP={'.arena','.cache','.git','.local','.mypy_cache','.next','.nox','.npm','.nuxt','.output','.parcel-cache','.pytest_cache','.ruff_cache','.svelte-kit','.tox','.turbo','.venv','.vite','__pycache__','build','coverage','dist','node_modules','out','target'}
FETCH_LEASE=timedelta(minutes=5)
UPLOAD_DIR='uploads'
FETCH_DIR='downloads'
def workspace_usage(root=None):
	root=Path(root or Path.cwd());total=0;count=0
	for(dirpath,dirnames,filenames)in os.walk(root,followlinks=False):
		dirnames[:]=[name for name in dirnames if name not in WORKSPACE_SKIP]
		for name in filenames:
			try:total+=(Path(dirpath)/name).stat().st_size
			except OSError:continue
			count+=1
	return{'bytes':total,'files':count,'documented_cap_bytes':SNAPSHOT_CAP_BYTES,'download_cap_bytes':MAX_FETCH}
FENCE=re.compile('^ {0,3}(`{3,}|~{3,})')
CHOICE=re.compile('^\\s*[-*]\\s+\\(([ xX]?)\\)\\s+(\\S.*?)\\s*$')
CHECKBOX=re.compile('^\\s*[-*]\\s+\\[([ xX]?)\\]\\s+(\\S.*?)\\s*$')
BLANK=re.compile('^(?:(.*?)[\\s:])?_{3,}\\s*$')
ANCHOR=re.compile('\\s*\\{#([a-zA-Z0-9_-]{1,80})\\}\\s*$')
BLOCK_START=re.compile('^ {0,3}(?:#{1,6}(?:\\s|$)|>|[-*+](?:\\s|$)|\\d+[.)](?:\\s|$)|(?:[-*_]\\s*){3,}$|`{3,}|~{3,})')
TASK_REMINDER='{remaining} tasks left.'
def fill_reminder(tail,remaining):
	if'{remaining}'in tail and remaining==0:return None
	count=f"{remaining} task"if remaining==1 else f"{remaining} tasks";return tail.replace('{remaining} tasks',count)
def reminder_tail(cursor,remaining,stale=0):
	for step in range(len(REMINDERS)):
		tail=fill_reminder(REMINDERS[(cursor+step)%len(REMINDERS)],remaining)
		if tail:
			if tail=='Unpublish stale reports.'and stale:return f"Unpublish {stale} stale report{'s'if stale!=1 else''}."
			return tail
	return REMINDERS[cursor%len(REMINDERS)]
REMINDERS='Refresh: ARENA.md, SKILL.md.','`task-list` at turn start. Update it as work changes.','Take the smallest open task next.','Always push.','`ask_user` on GH_TOKEN failure.','Keep docs terse but clear.','Ask questions ASAP in reports.','Publish your reports.','Never end a turn with unblocked tasks.','Unpublish stale reports.','End the turn with `poll`.','Grep-verify each edit.',TASK_REMINDER,'Rebase `origin/main` before pushing; empty checks mean it moved.',"Check the PR's CI before ending a pushed turn; read the run's verdict on a later call, never a loop in the call.",'Run `arena-preview`, not the full path.'
REMINDER_CURSOR='reminder_cursor'
POLLS_SINCE_MESSAGE='polls_since_message'
GATE_THRESHOLD=6
STALE_REPORT_THRESHOLD=10
DEFAULT_PORT=8000
AGENT_KEY_META='agent_key'
AGENT_KEY_EXPIRY_SECONDS=1200
AGENT_KEY_EXPIRY_META='agent_key_expired_at'
AGENT_SEEN_META='agent_seen_at'
AGENT_CALL_ENDED_META='agent_call_ended_at'
TURN_ENDED_META='turn_ended_at'
REMOVED_META='state_removed_at'
REPORT_CHANGE_VIEW_SECONDS=60
AGENT_KEY_RE=re.compile('[A-Za-z0-9_-]{20,64}\\Z')
AGENT_HOST_RE=re.compile('https://[A-Za-z0-9.-]+\\Z')
def now():return datetime.now(timezone.utc).isoformat()
def workspace_branch(root=None):
	try:head=(Path(root or Path.cwd())/'.git'/'HEAD').read_text(encoding='utf-8').strip()
	except OSError:return''
	prefix='ref: refs/heads/';return head[len(prefix):]if head.startswith(prefix)else''
def clear_skip_poll(db):db.execute('DELETE FROM meta WHERE key = ?',(SKIP_POLL_META,))
def reset_poll_count(db):
	if gate_pending_count(db)==1:db.execute("INSERT OR REPLACE INTO meta VALUES (?, '0')",(POLLS_SINCE_MESSAGE,))
def meta_number(db,key):row=db.execute('SELECT value FROM meta WHERE key = ?',(key,)).fetchone();value=str(row[0])if row else'';return int(value)if value.isdigit()else 0
def gate_pending_count(db):return db.execute('SELECT (SELECT count(*) FROM notes WHERE acknowledged_at IS NULL AND quiet = 0) + (SELECT count(*) FROM submissions WHERE acknowledged_at IS NULL)').fetchone()[0]
def main_identical(root='.'):
	def git(*arguments):return subprocess.run(['git',*arguments],cwd=root,capture_output=True,text=True,check=False)
	try:
		for ref in('origin/main','HEAD'):
			if git('rev-parse','--verify','--quiet',ref).returncode:return False
		return git('diff','--quiet','origin/main','HEAD','--').returncode==0
	except OSError:return False
def repo_slug(root='.'):
	try:url=subprocess.run(['git','config','--get','remote.origin.url'],cwd=root,capture_output=True,text=True,check=False).stdout.strip()
	except OSError:return''
	match=re.search('[:/]([^/:]+/[^/]+?)(?:\\.git)?$',url);return match.group(1)if match else''
FAILED_CONCLUSIONS='failure','cancelled','timed_out'
def pr_failed_check(root='.'):
	branch=workspace_branch(root);slug=repo_slug(root)
	if not branch or not slug:return''
	try:
		listed=subprocess.run(['gh','pr','list','--head',branch,'--json','headRefOid','--jq','.[0].headRefOid'],cwd=root,capture_output=True,text=True,check=False)
		if listed.returncode:return None
		head=listed.stdout.strip()
		if not head:return''
		rows=subprocess.run(['gh','api',f"repos/{slug}/commits/{head}/check-runs",'--paginate','--jq','.check_runs[] | [.name, .started_at, .conclusion] | @tsv'],cwd=root,capture_output=True,text=True,check=False)
		if rows.returncode:return None
		newest={}
		for row in rows.stdout.splitlines():
			name,_,rest=row.partition('\t');stamp,_,conclusion=rest.partition('\t')
			if name and(name not in newest or stamp.rstrip('Z')>newest[name][0]):newest[name]=stamp.rstrip('Z'),conclusion
		failed=[name for(name,(_,conclusion))in newest.items()if conclusion in FAILED_CONCLUSIONS];return', '.join(failed)
	except OSError:return None
def pr_check_once(result,root):
	failed=pr_failed_check(root)
	if failed:result.append(failed)
SESSION_ID_TABLES=('notes','note'),('submissions','answer'),('reports','report'),('tasks','task')
HEX7_RE=re.compile('[0-9a-f]{7}')
MINT_ATTEMPTS=16
DIFF_FILE_RE=re.compile('^\\+\\+\\+ b/(.+)$')
DIFF_HUNK_RE=re.compile('^@@ -\\S+ \\+(\\d+)')
def session_id_tokens(store):
	tokens={}
	with closing(store.connect())as db:
		for(table,label)in SESSION_ID_TABLES:
			for row in db.execute(f"SELECT id FROM {table}").fetchall():
				ident=row['id'];tokens.setdefault(ident,(label,ident))
				if NOTE_ID.fullmatch(ident):tokens.setdefault(ident[:6],(label,ident))
				head=ident.split('-',1)[0]
				if HEX7_RE.fullmatch(head):tokens.setdefault(head,(label,ident))
	return tokens
def staged_session_id_hits(store,root='.'):
	tokens=session_id_tokens(store)
	if not tokens:return[]
	try:diff=subprocess.run(['git','diff','--cached','-U0','--no-color'],cwd=root,capture_output=True,text=True,check=False)
	except OSError:return[]
	if diff.returncode:return[]
	patterns={token:re.compile(f"(?<![0-9A-Za-z]){re.escape(token)}(?![0-9A-Za-z])")for token in tokens};hits=[];path=None;number=0
	for line in diff.stdout.splitlines():
		named=DIFF_FILE_RE.match(line)
		if named:path=named.group(1);continue
		hunk=DIFF_HUNK_RE.match(line)
		if hunk:number=int(hunk.group(1))-1;continue
		if not line.startswith('+')or line.startswith('+++'):continue
		number+=1
		for(token,pattern)in patterns.items():
			if pattern.search(line):kind,ident=tokens[token];hits.append((path,number,token,kind,ident))
	return hits
def commit_id_report(hits):
	if not hits:return None
	lines=['COMMIT BLOCKED: the staged change cites a session-local ID. That ID exists only in this session, so a reader of the repository cannot resolve it.']
	for(path,number,token,kind,ident)in hits:named=ident if token==ident else f"{token} of {ident}";lines.append(f"  {path}:{number}: {kind} {named}")
	lines.append('Strip the citation, then commit again.');return'\n'.join(lines)
def new_id():return uuid.uuid4().hex[:6]
def seconds_since(value):
	if not value:return None
	try:return(datetime.now(timezone.utc)-datetime.fromisoformat(value)).total_seconds()
	except ValueError:return None
def clip_stamp(value):return value[:19]if value else value
def at_or_after(value,other):
	if not value or not other:return False
	return clip_stamp(value)>=clip_stamp(other)
def import_stamp(value):
	if not value:return None
	try:parsed=datetime.fromisoformat(str(value))
	except ValueError:return None
	if parsed.tzinfo is None:parsed=parsed.replace(tzinfo=timezone.utc)
	return parsed
def identifier(value):
	if not isinstance(value,str)or not IDENTIFIER.fullmatch(value):raise ValueError('ID must contain 1–80 letters, digits, underscores or hyphens')
	return value
def owner_text(text):
	if not isinstance(text,str)or not text.strip():raise ValueError('Enter a nonblank note')
	return text
def note_text(text):
	if not isinstance(text,str)or not text.strip():raise ValueError('Enter a nonblank note')
	return text
def when(value):
	try:datetime.fromisoformat(str(value))
	except ValueError as error:raise ValueError(f"Not a timestamp: {value!r}")from error
	return str(value)
def restore_receipt(acknowledged_at,ack_kind,ack_text,ack_edited_at=None):
	carried=acknowledged_at,ack_kind,ack_text
	if all(value is None for value in carried):
		if ack_edited_at is not None:raise ValueError('An edited receipt needs an acknowledgement')
		return None,None,None,None
	if any(value is None for value in carried):raise ValueError('A restored receipt carries its stamp, kind and text, or none of them')
	if ack_kind not in{'note','reply'}:raise ValueError('Every acknowledgement is a note or a reply, with its text')
	return when(acknowledged_at),ack_kind,note_text(ack_text),when(ack_edited_at)if ack_edited_at is not None else None
def restore_replies(replies,acknowledged):
	if not replies:return None
	if not acknowledged:raise ValueError('Appended replies need an acknowledgement')
	if not isinstance(replies,list):raise TypeError('Appended replies are a list')
	checked=[]
	for item in replies:
		if not isinstance(item,dict)or item.get('kind')not in{'note','reply'}:raise ValueError('Every appended reply is a note or a reply, with its text and stamp')
		checked.append({'kind':item['kind'],'text':note_text(item.get('text')),'at':when(item.get('at'))})
	return json.dumps(checked,ensure_ascii=False)
def replies_list(value):return json.loads(value)if value else[]
def message_row(row):
	record=dict(row);record.pop('seq',None)
	if'replies'in record:record['replies']=replies_list(record['replies'])
	return record
def reply_reference(text):match=REPLY_REFERENCE.match(text or'');return match.group(1)if match else None
def reply_thread(record,notes,pending_ids):
	thread,seen=[],{record['id']};target=reply_reference(record.get('text'))
	while target:
		parent=notes.get(target)
		if parent is None:
			matches=[note for(note_id,note)in notes.items()if note_id.startswith(target)]
			if len(matches)!=1:break
			parent=matches[0]
		parent_id=parent['id']
		if parent_id in seen or parent_id in pending_ids:break
		thread.append(parent);seen.add(parent_id);target=reply_reference(parent.get('text'))
	return list(reversed(thread))
def restore_reply_seen_count(value,replies):
	if value is None:return 0
	if type(value)is not int or value<0:raise ValueError('A saved viewed-reply count is a nonnegative integer')
	if value>len(replies_list(replies)):raise ValueError('A saved viewed-reply count exceeds the stored replies')
	return value
def submission_text(text):
	if not isinstance(text,str)or not text.strip():raise ValueError('A report submission carries at least one answer')
	return text
def slug(text,used):
	base=re.sub('[^a-z0-9]+','-',text.lower()).strip('-')[:60]or'field';candidate,suffix=base,2
	while candidate in used:candidate=f"{base}-{suffix}";suffix+=1
	used.add(candidate);return candidate
def prompt_text(line):text=re.sub('^\\s*(?:[-*+]\\s+|#+\\s+|>\\s+|\\d+[.)]\\s+)','',line).strip();return text.strip('*_` ').rstrip(':').strip()
def custom_label(option):found=BLANK.match(option);return None if not found else prompt_text(found.group(1)or'')or'Other'
def custom_answer(field,value):
	if not isinstance(value,str):return False
	for option in field['options']:
		label=custom_label(option)
		if label is None or not value.startswith(f"{label}: "):continue
		typed=value[len(label)+2:]
		if typed.strip():return True
	return False
def option_continuation(line):
	if not line.strip()or BLOCK_START.match(line):return False
	return not BLANK.match(line)and not ANCHOR.match(line)
def parse_fields(markdown):
	lines=markdown.splitlines();blocks,chunk,questions,used=[],[],[],set();fence,prompt,anchor,index,position=None,'',None,0,0
	while position<len(lines):
		line=lines[position]
		if fence is not None:
			chunk.append(line)
			if line.strip().startswith(fence):fence=None
			position+=1;continue
		opening=FENCE.match(line)
		if opening:fence=opening.group(1);chunk.append(line);position+=1;continue
		kind='choice'if CHOICE.match(line)else'checkbox'if CHECKBOX.match(line)else None;blank=None if kind else BLANK.match(line)
		if not kind and not blank:
			chunk.append(line)
			if line.strip():
				prompt,anchor=prompt_text(ANCHOR.sub('',line)),None;found=ANCHOR.search(line)
				if found:anchor=found.group(1);chunk[-1]=ANCHOR.sub('',line)
			position+=1;continue
		index+=1
		if kind:
			pattern=CHOICE if kind=='choice'else CHECKBOX;options,default=[],[]
			while position<len(lines):
				item=pattern.match(lines[position])
				if not item:break
				preselected=item.group(1).lower()=='x';options.append(item.group(2))
				if preselected:default.append(item.group(2))
				position+=1
				while position<len(lines)and option_continuation(lines[position]):
					options[-1]=f"{options[-1]} {lines[position].strip()}"
					if preselected:default[-1]=options[-1]
					position+=1
			if len(set(options))!=len(options):raise ValueError(f"Field '{prompt or index}' repeats an option; make each unique")
			question={'type':kind,'options':options,'default':default}
		else:
			label=(blank.group(1)or'').strip();question={'type':'text','default':[]}
			if label:
				prompt,anchor=prompt_text(ANCHOR.sub('',label)),None;found=ANCHOR.search(label)
				if found:anchor=found.group(1)
			position+=1
		question['prompt']=prompt or f"Field {index}";question['id']=anchor if anchor and anchor not in used else slug(question['prompt'],used);used.add(question['id']);anchor=None;blocks.append(('markdown','\n'.join(chunk)));blocks.append(('field',question));chunk=[];questions.append(question)
	blocks.append(('markdown','\n'.join(chunk)))
	if questions:Store.validate_fields(questions)
	return blocks,questions
def field_html(question):
	prompt=html.escape(question['prompt'],quote=True);name=html.escape(question['id'],quote=True);body=f'<div class="question" data-field="{name}"';body+=f' data-type="{question["type"]}">';body+=f'<small class="question-id">Question ID: <code>{name}</code></small>'
	if question['type']=='text':return f'{body}<textarea class="answer-text" rows="2" placeholder="Answer" aria-label="{prompt}"></textarea></div>'
	control='radio'if question['type']=='choice'else'checkbox';group=f'<div class="options" role="group" aria-label="{prompt}">'
	for option in question['options']:
		value=html.escape(option,quote=True);checked=' checked'if option in question['default']else'';label=custom_label(option)
		if label is None:group+=f'<label class="option"><input type="{control}" name="{name}" value="{value}"{checked}> {html.escape(option)}</label>';continue
		named=html.escape(label,quote=True);group+=f'<label class="option"><input type="{control}" name="{name}" value="{value}"{checked} data-label="{named}" aria-label="{named}"><textarea class="custom-text" rows="1" data-custom="{named}" placeholder="{named}:" aria-label="{named}, your own answer"></textarea></label>'
	return f"{body}{group}</div></div>"
TASK_STATUSES='upcoming','finished'
TASK_ID=re.compile('^[a-z0-9][a-z0-9-]{0,63}$')
NOTE_ID=re.compile('^[0-9a-f]{6,7}(?:-[0-9a-f]+)?$')
MAX_TASK_TITLE=200
MAX_TASK_DETAIL=2000
MAX_TASK_DETAILS=40
TASK_STEP_MAX=120
ECHO_DETAIL=200
TASK_COLUMNS='id, title, details, status, position, updated_at, completed_at, blocked, report_id'
TASK_ORDER="status DESC, CASE WHEN status = 'finished' THEN completed_at END DESC, CASE WHEN status = 'finished' THEN id END DESC, (blocked = 0 OR report_id IS NULL), position, id"
RENAME_TARGETS={'note':('notes',(('uploads','note_id'),)),'answer':('submissions',()),'report':('reports',(('submissions','report_id'),('tasks','report_id'))),'task':('tasks',(('notes','task_id'),('submissions','task_id')))}
RENAME_TABLES='notes','submissions','reports','tasks'
CLI_DESCRIPTION='Notes, reports, tasks and the preview server for Arena steering.'
SAVED_STATE='saved-state.ndjson'
NOTE_LINE_KEYS='id','text','at','acknowledged_at','ack_kind','ack_text','ack_edited_at','replies','ack_edited_seen_count','seen_at','task_id'
TASK_LINE_KEYS='id','title','details','status','completed_at','order','report_id'
SUBMISSION_LINE_KEYS='id','report_id','text','at','acknowledged_at','ack_kind','ack_text','ack_edited_at','replies','ack_edited_seen_count','seen_at','task_id'
REPORT_LINE_KEYS='id','title','markdown','published_at','seen_at'
def task_row(row):
	shaped={'id':row[0],'title':row[1],'details':json.loads(row[2]),'status':row[3],'order':row[4],'updated_at':row[5],'completed_at':row[6]if len(row)>6 else None,'blocked':bool(row[7])if len(row)>7 else False}
	if len(row)>8 and row[8]:shaped['report_id']=row[8]
	return shaped
def saved_note_line(record):
	if not isinstance(record,dict):raise TypeError('Every saved note is an object')
	return{key:record.get(key)for key in NOTE_LINE_KEYS}
def saved_answer_line(record):
	if not isinstance(record,dict):raise TypeError('Every saved answer is an object')
	return{key:record.get(key)for key in SUBMISSION_LINE_KEYS}
def saved_report_line(record):
	if not isinstance(record,dict):raise TypeError('Every saved report is an object')
	return{key:record.get(key)for key in REPORT_LINE_KEYS}
def state_ndjson(lines):return''.join(json.dumps(line,ensure_ascii=False)+'\n'for line in lines)
def saved_task_line(record):
	if not isinstance(record,dict):raise TypeError('Every saved task is an object')
	line={key:record.get(key)for key in TASK_LINE_KEYS};line['details']=[str(item)for item in record.get('details')or[]]
	if not line.get('report_id'):line.pop('report_id',None)
	return line
def upload_name(name):
	cleaned=Path(str(name or'')).name.strip()
	if not cleaned:raise ValueError('An upload needs a file name')
	if any(ord(char)<32 or ord(char)==127 for char in cleaned):raise ValueError('A file name must not contain control characters')
	return cleaned
def header_filename(name):return str(name).replace('\n','_').replace('\r','_').replace('"','_')
def fetch_url(value,*,agent=True):
	if not isinstance(value,str)or not value.strip()or agent and len(value)>2048:raise ValueError('Enter one HTTPS URL of at most 2048 characters')
	value=value.strip()
	if any(ord(char)<33 or ord(char)==127 for char in value):raise ValueError('A download URL must not contain spaces or control characters')
	try:
		parsed=urlsplit(value)
		if parsed.scheme.lower()!='https'or not parsed.hostname or parsed.port==0:raise ValueError('Only HTTPS download URLs are allowed')
		if parsed.username is not None or parsed.password is not None:raise ValueError('Do not put credentials in a download URL')
	except ValueError as error:raise ValueError(f"Invalid HTTPS download URL: {error}")from error
	return urlunsplit(parsed._replace(fragment=''))
def fetch_row(row,directory):item=dict(row);item.pop('claim',None);item['allow_proxy']=bool(item['allow_proxy']);file=item['file'];path=directory/FETCH_DIR/file if file else None;item['path']=str(path)if path else None;item['present']=bool(path and path.is_file());return item
def upload_type(content_type):cleaned=str(content_type or'').split(';')[0].strip()[:120];return cleaned or'application/octet-stream'
class FileBody:
	def __init__(self,stream):self.stream=stream;self.path=None;self.size=0;self.hash=hashlib.sha256()
	def __len__(self):return self.size
	def write(self,chunk):self.stream.write(chunk);self.size+=len(chunk);self.hash.update(chunk)
	def digest(self):return self.hash.hexdigest()
	def save(self,target):
		source=self.path.open('rb')if self.path else self.stream
		with closing(source)if self.path else nullcontext(source):
			source.seek(0)
			with target.open('wb')as output:shutil.copyfileobj(source,output,65536)
def body_digest(data):return data.digest()if isinstance(data,FileBody)else hashlib.sha256(data).hexdigest()
def save_body(target,data):
	with tempfile.NamedTemporaryFile(dir=target.parent,delete=False)as staged:temporary=Path(staged.name)
	try:
		if isinstance(data,FileBody):data.save(temporary)
		else:temporary.write_bytes(data)
		temporary.chmod(384);os.replace(temporary,target)
	finally:temporary.unlink(missing_ok=True)
class RequestBody:
	def __init__(self,stream,length):self.stream=stream;self.remaining=length;self.buffer=b''
	def fill(self):
		if self.remaining==0:return False
		chunk=self.stream.read(min(65536,self.remaining))
		if not chunk:raise ValueError('Incomplete request body')
		self.remaining-=len(chunk);self.buffer+=chunk;return True
	def take(self,size):
		while len(self.buffer)<size:
			if not self.fill():raise ValueError('Incomplete multipart body')
		result,self.buffer=self.buffer[:size],self.buffer[size:];return result
	def until(self,marker,write,boundary=False):
		while True:
			index=self.buffer.find(marker)
			if index>=0:
				end=index+len(marker)
				if boundary:
					while len(self.buffer)<end+2 and self.fill():pass
					if self.buffer[end:end+2]not in{b'\r\n',b'--'}:write(self.buffer[:index+1]);self.buffer=self.buffer[index+1:];continue
				write(self.buffer[:index]);self.buffer=self.buffer[end:];return
			keep=len(marker)-1
			if len(self.buffer)>keep:write(self.buffer[:-keep]);self.buffer=self.buffer[-keep:]
			if not self.fill():raise ValueError('Incomplete multipart body')
@contextmanager
def stream_note_attachments(content_type,stream,length):
	if any(char in content_type for char in'\r\n'):raise ValueError('Invalid multipart boundary')
	header=BytesParser(policy=policy.default).parsebytes(b'Content-Type: '+content_type.encode('ascii')+b'\r\n\r\n');boundary=header.get_boundary()
	if not boundary or not re.fullmatch("[0-9A-Za-z'()+_,./:=? -]{1,70}",boundary)or boundary.endswith(' '):raise ValueError('Invalid multipart boundary')
	reader=RequestBody(stream,length);delimiter=b'--'+boundary.encode('ascii')
	if reader.take(len(delimiter)+2)!=delimiter+b'\r\n':raise ValueError('Invalid multipart opening')
	with tempfile.TemporaryDirectory()as staging:
		fields,files={},[]
		while True:
			headers=bytearray();reader.until(b'\r\n\r\n',headers.extend);part=BytesParser(policy=policy.default).parsebytes(bytes(headers)+b'\r\n\r\n')
			for key in('Content-Disposition','Content-Type','Content-Transfer-Encoding'):
				if len(part.get_all(key,[]))>1:raise ValueError('Duplicate attachment header')
			encoding=str(part.get('Content-Transfer-Encoding','binary')).strip().lower()
			if encoding not in{'binary','8bit'}:raise ValueError('Unsupported attachment transfer encoding')
			name=part.get_param('name',header='content-disposition')
			if part.defects or part.is_multipart()or part.get_content_disposition()!='form-data'or name not in{'id','text','file'}:raise ValueError('Unexpected note attachment field')
			if name!='file'and(name in fields or part.get_filename()is not None):raise ValueError('Duplicate or invalid note attachment field')
			if name=='file':
				path=Path(staging)/str(len(files));stream=path.open('wb');body=FileBody(stream);body.path=path
				def write(chunk,body=body):body.write(chunk)
				with stream:reader.until(b'\r\n'+delimiter,write,boundary=True)
				if not body:raise ValueError('An upload must not be empty')
				files.append((part.get_filename(),part.get_content_type(),body))
			else:value=bytearray();reader.until(b'\r\n'+delimiter,value.extend,boundary=True);fields[name]=value.decode('utf-8')
			ending=reader.take(2)
			if ending==b'--':
				if(reader.buffer or reader.remaining)and reader.take(2)!=b'\r\n':raise ValueError('Invalid closing multipart delimiter')
				while reader.fill():reader.buffer=b''
				break
			if ending!=b'\r\n':raise ValueError('Invalid multipart delimiter')
		if set(fields)!={'id','text'}or not files:raise ValueError('Send one note ID, text and at least one file')
		yield(fields['id'],fields['text'],files)
def upload_row(row,directory):path=directory/UPLOAD_DIR/row['file'];record=dict(row)|{'path':str(path),'present':path.exists()};record.pop('seq',None);return record
def add_note_attachments(note,records):
	if records:note['attachment_name']=records[0]['name'];note['attachment_path']=records[0]['path'];note['attachments']=records
	return note
def bare_id(token):return str(token or'').strip().strip('`').strip()
def format_size(size):
	if size<1024:return f"{size} B"
	if size<1024**2:return f"{short_number(size/1024)} KB"
	return f"{short_number(size/1024**2)} MB"
def short_number(value):text=f"{value:.1f}";return text.removesuffix('.0')
def attachment_line(record):
	line=f"{record.get('name','')}: {record.get('path','')}"
	if not record.get('present'):return line+' (missing)'
	size=record.get('size');return line+(f" ({format_size(size)})"if isinstance(size,int)else'')
def format_pending_record(item):
	kind=item.get('kind')or'note';display_kind='answer'if kind=='report'else kind;header='`'+str(item.get('id',''))[:6]+'`'
	if display_kind!='note':header+=f" {display_kind}"
	if item.get('quiet'):header+=' quiet (no ack required)'
	if item.get('report_id'):header+=f" report {item['report_id']}"
	lines=[header];text=item.get('text')or''
	if text:lines.append(text)
	attachments=item.get('attachments')or[]
	if attachments:names=', '.join(attachment_line(record)for record in attachments);lines.append(f"attachments: {names}")
	return'\n'.join(lines)
def format_pending_item(item):thread=item.get('thread')or[];return'\n'.join([*(format_pending_record(record)for record in thread),format_pending_record(item)])
def format_read(listing):
	checked_at=listing.get('checked_at');pending=listing.get('pending')or[];count=len(pending)
	if checked_at:first=f"Inbox {checked_at}: {count} pending"
	else:first=f"Inbox: {count} pending"
	if count==0:return first
	return'\n'.join([first,*(format_pending_item(item)for item in pending)])
def current_task(tasks):
	for task in tasks:
		if task.get('status')=='upcoming'and not task.get('blocked'):return task
	return None
def format_task_list(tasks,full=False):
	shown=tasks if full else[t for t in tasks if t.get('status')=='upcoming'];lines=[f"Tasks {len(shown)}"if full else f"Tasks {len(shown)} upcoming"];head=None if full else current_task(shown)
	for task in shown:
		order=task.get('order');status=task.get('status')
		if head is not None and task.get('id')==head.get('id'):status='current'
		id_=task.get('id');blocked=task.get('blocked');report_id=task.get('report_id');blocked_mark='*'if blocked else'';line=f"{order}{blocked_mark}  {status} {id_}"
		if report_id:line+=f" report {report_id}"
		lines.append(line)
	return'\n'.join(lines)
def format_task(record):
	id_=record.get('id');order=record.get('order');status=record.get('status');report_id=record.get('report_id');blocked=record.get('blocked');title=record.get('title')or'';details=record.get('details')or[];cut=[detail[:ECHO_DETAIL]+('…'if len(detail)>ECHO_DETAIL else'')for detail in details];first=f"task {id_}, order {order}, status {status}"
	if report_id:first+=f", report {report_id}"
	if blocked:first+=', blocked'
	lines=[first,title]+[f"- {d}"for d in cut];return'\n'.join(lines)
def format_key(record):
	lines=[]
	if record.get('key'):lines.append(f"key {record['key']}")
	if record.get('host'):lines.append(f"host {record['host']}");lines.append(f"use fetch_page on {record['host']}/v1/<route>?key=<key>, never bash")
	if record.get('at'):lines.append(f"at {record['at']}")
	return'\n'.join(lines)if lines else''
def format_fetch_job(job):
	id_=job.get('id');url=job.get('url');allow_proxy=job.get('allow_proxy');approval=job.get('approval');status=job.get('status');parts=[f"Queued {id_} {url}"]
	if allow_proxy is not None:parts[0]+=f" proxy={allow_proxy}"
	if approval:parts[0]+=f" approval={approval}"
	if status:parts[0]+=f" status={status}"
	return'\n'.join(parts)
def format_import_receipt(receipt):notes=receipt.get('notes',0);answers=receipt.get('answers',0);reports=receipt.get('reports',0);tasks=receipt.get('tasks',0);return f"Imported notes {notes}, answers {answers}, reports {reports}, tasks {tasks}"
def format_clear_state(counts):parts=[f"{k} {v}"for(k,v)in counts.items()];return'Cleared '+', '.join(parts)if parts else'Cleared'
def require_server(store):
	port=store.meta_value('port')
	if not port:return
	with closing(socket.socket())as probe:
		probe.settimeout(1)
		if probe.connect_ex(('127.0.0.1',int(port)))==0:return
	raise ValueError(f"preview server is down; start it before polling: arena-preview serve --port {port}")
def print_read(store):
	listing=store.read();print(format_read(listing),flush=True)
	if listing['pending']:store.mark_seen([item['id']for item in listing['pending']]);store.mark_reports_agent_seen([item.get('report_id')for item in listing['pending']])
POLL_INTERVAL=1
POLL_MAX_LOOPS=1800
POLLING_META='polling_at'
POLL_SINCE_META='polling_since'
POLLING_FRESH_SECONDS=5.
SKIP_POLL_META='skip_poll_at'
INERT_COMMANDS='cd','export','set','unset','true',':','source','.','trap','shopt','umask'
def unquote_commands(line):
	kept=[];quote='';index=0;text=str(line or'').replace('\r','\n')
	while index<len(text):
		char=text[index]
		if quote:
			if char=='\\'and quote=='"':index+=2;continue
			if quote=='"'and(text.startswith('$(',index)or char=='`'):kept.append(' $(')
			if char==quote:quote=''
			index+=1;continue
		if char in("'",'"'):quote=char;index+=1;continue
		if char=='\\'and index+1<len(text):kept.append(text[index+1]);index+=2;continue
		kept.append(char);index+=1
	return''.join(kept)
INLINE_ACK_RE=re.compile('--(?:reply|note)\\s+\\"')
def tick_warning(line):
	found=INLINE_ACK_RE.search(line or'')
	if not found or'`'not in line[found.end():]:return None
	return'arena-preview gate: that ack text sits in double quotes, so the shell runs each backtick as a command and the ticks vanish. Single-quote the text, or pass --reply-file <path>.'
SERVE_PORT_RE=re.compile('(?:arena-preview|preview\\.py)\\s+serve\\b[^\\n]*?--port(?:=|\\s+)(\\d+)')
def serve_warning(line):
	found=SERVE_PORT_RE.search(line or'')
	if not found or int(found.group(1))==DEFAULT_PORT:return None
	return f"arena-preview gate: that serve names port {found.group(1)}, not the standard {DEFAULT_PORT}. The owner's page and the poll follow the standard port. Name another only when 8000 is taken, and say so."
EGRESS_ALLOWLIST=frozenset(('github.com','api.github.com','codeload.github.com','registry.npmjs.org','pypi.org','files.pythonhosted.org','dns.google'))
FETCH_COMMAND_RE=re.compile('(?:^|[\\s;&|(])(?:curl|wget|git\\s+(?:clone|fetch|ls-remote|pull|push))\\b')
URL_HOST_RE=re.compile('(?:https?|ftp)://([^/\\s\\"\'@?]+)')
def egress_warning(line):
	text=line or''
	if not FETCH_COMMAND_RE.search(text):return None
	hosts=[]
	for found in URL_HOST_RE.finditer(text):
		host=found.group(1).split(':',1)[0].lower()
		if host and host not in EGRESS_ALLOWLIST and host not in hosts:hosts.append(host)
	if not hosts:return None
	names=', '.join(hosts);return f"arena-preview gate: {names} sits outside this sandbox's egress allowlist. The TLS handshake fails with SSL_ERROR_SYSCALL, which reads as a transient fault and never clears on a retry. Read it through the fetch_page tool instead."
def first_blocking_piece(line):
	text=unquote_commands(line)
	for separator in('&&','||',';','|','\n'):text=text.replace(separator,'\x00')
	for piece in text.split('\x00'):
		tokens=[token for token in piece.split()if token]
		if not tokens:continue
		idx=0
		while idx<len(tokens)and'='in tokens[idx]and not tokens[idx].startswith('-'):idx+=1
		if idx>=len(tokens):continue
		if tokens[idx]=='bash':
			idx+=1
			while idx<len(tokens)and tokens[idx].startswith('-'):
				idx+=1
				if idx<len(tokens)and tokens[idx-1]=='-c':break
			if idx>=len(tokens):continue
		head=tokens[idx].rsplit('/',1)[-1]
		if head in INERT_COMMANDS:continue
		if head in('arena-preview','preview.py'):
			rest=tokens[idx+1:]
			if any(token.startswith('$(')or'`'in token for token in rest):return piece.strip()
			continue
		return piece.strip()
	return None
def quiet_inbox_line(line):return first_blocking_piece(line)is None
GATE_NOISE='tail','grep','head'
RUN_NAME=re.compile('[A-Za-z0-9._-]{1,64}')
RUN_TAIL_LINES=40
def run_record_path(state_dir,name):return Path(state_dir)/'runs'/f"{name}.json"
ROUTE_START_FAILED='routed child failed to start; rerun the gate'
ROUTE_CHILD_EXITED='routed child exited without recording a verdict; rerun the gate'
def write_run_record(path,record):
	path=Path(path);path.parent.mkdir(parents=True,exist_ok=True);descriptor,temporary=tempfile.mkstemp(prefix=f".{path.name}.",dir=path.parent,text=True)
	try:
		with os.fdopen(descriptor,'w',encoding='utf-8')as file:file.write(json.dumps(record,ensure_ascii=False,indent=1)+'\n');file.flush();os.fsync(file.fileno())
		os.replace(temporary,path)
	except OSError:
		try:Path(temporary).unlink()
		except OSError:pass
		raise
def process_start_identity(pid):
	if isinstance(pid,bool)or not isinstance(pid,int)or pid<1:return None
	try:stat=(Path('/proc')/str(pid)/'stat').read_text(encoding='utf-8')
	except OSError:return None
	close=stat.rfind(')');fields=stat[close+2:].split()if close>=0 else[];return fields[19]if len(fields)>19 else None
def routed_child_matches(record):pid=record.get('pid');started=record.get('pid_started_at');return isinstance(pid,int)and not isinstance(pid,bool)and isinstance(started,str)and bool(started)and process_start_identity(pid)==started
def terminal_route_failure(path,record,verdict):failed=dict(record);elapsed=detached_seconds(failed.get('started_at'));failed.update({'exit':1,'seconds':elapsed if elapsed is not None else 0,'finished_at':now(),'verdict':verdict,'tail':[verdict],'announced':False});write_run_record(path,failed);return failed
def reconcile_routed_record(path,record):
	if record.get('detached')and record.get('exit')is None and not routed_child_matches(record):return terminal_route_failure(path,record,ROUTE_CHILD_EXITED)
	return record
WORKTREE_DIR='worktrees'
WORKTREE_ACTIONS={'gate':'a gate','commit':'a commit','push':'a push'}
def worktree_record_path(state_dir,name):return Path(state_dir)/WORKTREE_DIR/f"{name}.json"
def json_record(path):
	try:record=json.loads(Path(path).read_text(encoding='utf-8'))
	except(OSError,ValueError):return None
	return record if isinstance(record,dict)else None
def routed_record(state_dir,name):
	if not RUN_NAME.fullmatch(name or''):raise ValueError('Gate names take 1 to 64 letters, digits, dots, dashes or underscores.')
	path=run_record_path(state_dir,name);record=json_record(path)
	if record is None:raise ValueError(f"No routed gate named {name}.")
	return reconcile_routed_record(path,record)
def git_worktree(cwd,*args,input=None,allowed=(0,)):
	try:result=subprocess.run(['git',*args],cwd=str(cwd),input=input,capture_output=True,text=True,check=False)
	except OSError as error:raise ValueError(f"Cannot run Git: {error}")from None
	if result.returncode not in allowed:detail=(result.stderr or result.stdout).strip();raise ValueError(detail or f"Git {' '.join(args)} failed")
	return result.stdout
def git_worktree_root(cwd=None):root=git_worktree(cwd or Path.cwd(),'rev-parse','--show-toplevel').strip();return Path(root).resolve()
def under_path(path,parent):
	try:Path(path).resolve().relative_to(Path(parent).resolve())
	except ValueError:return False
	return True
def stored_worktree(state_dir,name):
	name=checked_worktree_name(name);record=json_record(worktree_record_path(state_dir,name))
	if record is None:raise ValueError(f"No worktree named {name}.")
	return record
def worktree_at(state_dir,cwd=None):
	current=Path(cwd or Path.cwd()).resolve();directory=Path(state_dir)/WORKTREE_DIR
	if not directory.is_dir():return None
	for path in sorted(directory.glob('*.json')):
		record=json_record(path);target=record.get('worktree')if record else None
		if isinstance(target,str)and under_path(current,target):return record
	return None
def worktree_warning(state_dir,action,cwd=None):
	record=worktree_at(state_dir,cwd)
	if record is None:return None
	label=WORKTREE_ACTIONS.get(action,action);return f"WORKTREE BLOCKED: {record.get('name')} is detached. Do not run {label} here. Run it from {record.get('root')}."
def checked_worktree_name(name):
	if not RUN_NAME.fullmatch(name or''):raise ValueError('Worktree names take 1 to 64 letters, digits, dots, dashes or underscores.')
	return name
def start_worktree(state_dir,name,destination,gate,allowed,cwd=None):
	name=checked_worktree_name(name)
	if not allowed or any(not pattern for pattern in allowed):raise ValueError('Give at least one --allow path glob.')
	current=Path(cwd or Path.cwd()).resolve();registered=worktree_at(state_dir,current)
	if registered is not None:raise ValueError(f"Start a worktree from the registered root: {registered.get('root')}.")
	root=git_worktree_root(current)
	if current!=root:raise ValueError('Start a worktree from the repository root.')
	destination=Path(destination).expanduser().resolve()
	if destination.exists():raise ValueError(f"Worktree path already exists: {destination}")
	if under_path(destination,root):raise ValueError('Put the detached worktree outside the repository root.')
	record_path=worktree_record_path(state_dir,name)
	if record_path.exists():raise ValueError(f"A worktree named {name} is already recorded.")
	running=routed_record(state_dir,gate)
	if not running.get('detached')or running.get('exit')is not None:raise ValueError(f"Gate {gate} is not running.")
	base=git_worktree(root,'rev-parse','HEAD').strip();destination.parent.mkdir(parents=True,exist_ok=True);git_worktree(root,'worktree','add','--detach',str(destination),base);record={'name':name,'root':str(root),'worktree':str(destination),'base':base,'gate':gate,'allow':list(allowed),'created_at':now()}
	try:record_path.parent.mkdir(parents=True,exist_ok=True);record_path.write_text(json.dumps(record,ensure_ascii=False,indent=1)+'\n',encoding='utf-8')
	except OSError as error:subprocess.run(['git','worktree','remove','--force',str(destination)],cwd=str(root),capture_output=True,check=False);raise ValueError(f"Cannot record worktree {name}: {error}")from None
	return record
def worktree_paths(worktree):tracked=git_worktree(worktree,'diff','--name-only','-z','HEAD');untracked=git_worktree(worktree,'ls-files','--others','--exclude-standard','-z');return sorted({path for path in(tracked+untracked).split('\x00')if path})
def collect_worktree(state_dir,name,cwd=None):
	record=stored_worktree(state_dir,name);root=Path(record.get('root','')).resolve()
	if Path(cwd or Path.cwd()).resolve()!=root:raise ValueError(f"Collect worktree {name} from its root: {root}")
	worktree=Path(record.get('worktree','')).resolve()
	if git_worktree_root(worktree)!=worktree:raise ValueError(f"Worktree path is not a Git root: {worktree}")
	if git_worktree(worktree,'rev-parse','HEAD').strip()!=record.get('base'):raise ValueError(f"Worktree {name} has a commit. Keep task changes uncommitted.")
	gate=routed_record(state_dir,record.get('gate'))
	if gate.get('exit')is None:raise ValueError(f"Gate {record.get('gate')} is still running.")
	if gate.get('exit')!=0:raise ValueError(f"Gate {record.get('gate')} did not pass.")
	allowed=record.get('allow')
	if not isinstance(allowed,list)or not all(isinstance(pattern,str)for pattern in allowed):raise ValueError(f"Worktree {name} has no valid allowed paths.")
	paths=worktree_paths(worktree)
	if not paths:raise ValueError(f"Worktree {name} has no changes.")
	outside=[path for path in paths if not any(fnmatchcase(path,pattern)for pattern in allowed)]
	if outside:raise ValueError('Worktree changes outside allowed paths: '+', '.join(outside))
	patch=git_worktree(worktree,'diff','--binary','--no-ext-diff','HEAD')
	for path in git_worktree(worktree,'ls-files','--others','--exclude-standard','-z').split('\x00'):
		if not path:continue
		patch+=git_worktree(worktree,'diff','--no-index','--binary','--','/dev/null',path,allowed=(0,1))
	return patch
def run_command(state_dir,name,argv):
	if not RUN_NAME.fullmatch(name or''):print('arena-preview route: the name takes 1 to 64 letters, digits, dot, dash or underscore.',flush=True);return 2
	if argv[:1]==['--']:argv=argv[1:]
	if not argv:print('arena-preview route: name a command after --, for example: route gates -- make check',flush=True);return 2
	started=time.monotonic();completed=subprocess.run(argv,capture_output=True,text=True,check=False);seconds=round(time.monotonic()-started,1);output=(completed.stdout+completed.stderr).splitlines();tail=output[-RUN_TAIL_LINES:];verdict=tail[-1]if tail else'(no output)';path=run_record_path(state_dir,name);existing=json_record(path)or{};record={**existing,'name':name,'argv':argv,'exit':completed.returncode,'seconds':seconds,'finished_at':now(),'verdict':verdict,'tail':tail,'announced':False};write_run_record(path,record)
	for line in tail:print(line,flush=True)
	print(f"run {name} done: exit {completed.returncode} {seconds}s {verdict}",flush=True);return completed.returncode
def live_runs(state_dir):
	names=[];runs=Path(state_dir)/'runs'
	if not runs.is_dir():return names
	for path in sorted(runs.glob('*.json')):
		record=json_record(path)
		if record is None:continue
		record=reconcile_routed_record(path,record)
		if record.get('detached')and record.get('exit')is None:names.append(record.get('name')or path.stem)
	return names
def skip_poll_ready(store):return store.skip_poll_requested()and not live_runs(store.path.parent)
def run_notices(state_dir,show_running=False):
	runs=Path(state_dir)/'runs'
	if not runs.is_dir():return 0
	records=[]
	for path in sorted(runs.glob('*.json')):
		record=json_record(path)
		if record is not None:records.append((path,reconcile_routed_record(path,record)))
	for(path,record)in records:
		if record.get('exit')is None or record.get('announced'):continue
		print(f"run {record.get('name')} done: exit {record.get('exit')} {record.get('seconds')}s {record.get('verdict')}",flush=True);record['announced']=True;write_run_record(path,record);return 1
	if not show_running:return 0
	for(_,record)in records:
		if record.get('exit')is not None or not record.get('detached'):continue
		elapsed=detached_seconds(record.get('started_at'))
		if elapsed is None or elapsed>DETACHED_STATUS_SECONDS:continue
		print(f"run {record.get('name')} running {elapsed}s",file=sys.stderr,flush=True);return 1
	return 0
SHELL_WRAPPER=re.compile('^(?:\\S*/)?(?:ba|z|da)?sh\\s+(?:-\\w+\\s+)*-c\\s+')
def shell_command_text(text):
	stripped=SHELL_WRAPPER.sub('',text.strip(),count=1)
	if len(stripped)>=2 and stripped[0]==stripped[-1]and stripped[0]in'\'"':stripped=stripped[1:-1]
	return stripped
def gate_line_hint(line):
	words={token for token in re.split('[^A-Za-z0-9_.-]+',line or'')};found=[name for name in GATE_NOISE if name in words]
	if not found:return None
	named=', '.join(f"`{name}`"for name in found);return f"This call runs {named}. Omit them: only a bare `arena-preview read` passes."
def poll_timeout_line(line):
	text=unquote_commands(line or'')
	for separator in('&&','||',';','|','\n'):text=text.replace(separator,'\x00')
	for piece in text.split('\x00'):
		tokens=[token for token in piece.split()if token]
		if not tokens or tokens[0].rsplit('/',1)[-1]!='timeout':continue
		words=tokens[1:]
		for(index,word)in enumerate(words):
			if word in('arena-preview','preview.py')and index+1<len(words)and words[index+1]=='poll':return True
	return False
PR_CHECK_COMMANDS=('pr','checks'),('run','watch')
PR_CHECK_WRAPPERS='env','command','timeout','xargs','nice','nohup','stdbuf','sudo'
PR_CHECK_NAMES=('pr','checks','pr-checks'),('run','watch','run-watch')
LONG_RUN_HEADS={'pytest':'pytest','py.test':'pytest','tox':'tox','nox':'nox'}
LONG_RUN_WORDS=(('npm','test'),'npm-tests'),(('npm','run','test*'),'npm-tests'),(('yarn','test'),'yarn-tests'),(('pnpm','test'),'pnpm-tests'),(('make','test'),'make-tests'),(('npx','jest'),'npx-tests'),(('npx','vitest'),'npx-tests'),(('npx','mocha'),'npx-tests'),(('npx','ava'),'npx-tests'),(('go','test'),'go-tests'),(('cargo','test'),'cargo-tests'),(('dotnet','test'),'dotnet-tests'),(('mvn','test'),'mvn-tests'),(('mvnw','test'),'mvn-tests'),(('gradle','test'),'gradle-tests'),(('gradlew','test'),'gradle-tests')
WORKTREE_STEP='NEXT TASK: a queued task that edits files continues in a worktree while this runs: `arena-preview worktree start <name> -- <command line>`.'
DETACHED_NOTICE='RUN DETACHED: `{name}` runs in the background, so the call ends clean; its verdict prints on a later call. '+WORKTREE_STEP
DETACHED_BLOCK='RUN BLOCKED: the background start failed, so the wait never left. Run `arena-preview route {name} -- <the same line>` instead.'
DETACHED_STATUS_SECONDS=7200
DETACH_DRY_RUN='ARENA_PREVIEW_DETACH_DRY'
def long_run_words(tokens,index,head):
	rest=[token for token in tokens[index+1:]if not token.startswith('-')]
	for(words,name)in LONG_RUN_WORDS:
		if head!=words[0]:continue
		span=rest[:len(words)-1]
		if len(span)!=len(words)-1:continue
		if all(token.startswith(word[:-1])if word.endswith('*')else token==word for(token,word)in zip(span,words[1:])):return name
	return None
def long_run_name(line):
	text=unquote_commands(line or'')
	for separator in('&&','||',';','|','\n','$(','`'):text=text.replace(separator,'\x00')
	for piece in text.split('\x00'):
		tokens=[token for token in piece.split()if token];index=0
		while index<len(tokens)and'='in tokens[index]and not tokens[index].startswith('-'):index+=1
		while index<len(tokens):
			head=tokens[index].lstrip('({"\'').rsplit('/',1)[-1]
			if head not in PR_CHECK_WRAPPERS:break
			index+=1
			while index<len(tokens)and(tokens[index].startswith('-')or'='in tokens[index]or tokens[index].rstrip('smhd').isdigit()):
				if head=='command'and re.fullmatch('-[pVv]*[Vv][pVv]*',tokens[index]):index=len(tokens);break
				index+=1
		if index>=len(tokens):continue
		head=tokens[index].lstrip('({"\'').rsplit('/',1)[-1]
		if head in INERT_COMMANDS or head=='bash':continue
		if head in LONG_RUN_HEADS:return LONG_RUN_HEADS[head]
		runner=long_run_words(tokens,index,head)
		if runner:return runner
		if head.startswith('python')and tuple(tokens[index+1:index+3])==('-m','pytest'):return'pytest'
		if head=='node'and tokens[index+1:index+2]==['--test']:return'node-tests'
		if head!='gh':continue
		for(position,word)in enumerate(tokens[index+1:-1]):
			for(first,second,name)in PR_CHECK_NAMES:
				if word==first and tokens[index+2+position]==second:return name
	return None
def pr_check_line(line):return long_run_name(line)is not None
def detached_seconds(started_at):
	try:
		started=datetime.fromisoformat(str(started_at))
		if started.tzinfo is None:started=started.replace(tzinfo=timezone.utc)
		return max(0,int((datetime.now(timezone.utc)-started).total_seconds()))
	except(TypeError,ValueError):return None
def write_detached_placeholder(state_dir,name,argv,pid=None,pid_started_at=None):
	path=run_record_path(state_dir,name);record={'name':name,'argv':list(argv),'exit':None,'seconds':None,'started_at':now(),'finished_at':None,'verdict':None,'tail':[],'detached':True,'announced':False}
	if isinstance(pid,int)and not isinstance(pid,bool)and pid>0:record['pid']=pid
	if isinstance(pid_started_at,str)and pid_started_at:record['pid_started_at']=pid_started_at
	write_run_record(path,record);return path
PR_CHECK_OTHER=0
PR_CHECK_RUNS=1
PR_CHECK_REFUSED=2
SHELL_BASENAMES='bash','sh','dash','ksh','zsh'
def agent_line(line):
	words=(line or'').split(' ');head=words[0].rsplit('/',1)[-1]if words else''
	if head not in SHELL_BASENAMES or len(words)<2:return line or''
	first=words[1]
	if not first.startswith('-')or'l'not in first:return line or''
	if'c'in first:rest=words[2:]
	elif words[2:3]==['-c']:rest=words[3:]
	else:return line or''
	return' '.join(rest).strip()or(line or'')
def pr_check_wait(state_dir,line,command=None):
	line=agent_line(line);warning=worktree_warning(state_dir,'gate')
	if warning:print(warning,file=sys.stderr,flush=True);return PR_CHECK_REFUSED
	name=long_run_name(line)
	if not name:return PR_CHECK_OTHER
	if command is not None and line.strip()!=command.strip():print(f"RUN BLOCKED: automatic routing would replay a mixed shell line. Use `arena-preview route {name} -- <remaining command>`; do not repeat completed commands.",file=sys.stderr,flush=True);return PR_CHECK_REFUSED
	if not detach_run(state_dir,name,['bash','-c',line]):print(DETACHED_BLOCK.format(name=name),file=sys.stderr,flush=True);return PR_CHECK_REFUSED
	print(DETACHED_NOTICE.format(name=name),file=sys.stderr,flush=True);return PR_CHECK_RUNS
def detach_run(state_dir,name,argv):
	path=run_record_path(state_dir,name);standing=json_record(path)
	if standing is not None:
		standing=reconcile_routed_record(path,standing)
		if standing.get('exit')is None:return True
	if os.environ.get(DETACH_DRY_RUN):pid=os.getpid();write_detached_placeholder(state_dir,name,argv,pid,process_start_identity(pid));return True
	path=write_detached_placeholder(state_dir,name,argv);child_env={key:value for(key,value)in os.environ.items()if key not in('BASH_ENV',DETACH_DRY_RUN,'ARENA_PREVIEW_ROUTE')};child_env['ARENA_PREVIEW_ROUTE']='1'
	try:child=subprocess.Popen([sys.executable,str(Path(__file__).resolve()),'route-run',name,'--',*argv],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,env=child_env,start_new_session=True,close_fds=True)
	except OSError:terminal_route_failure(path,json_record(path)or{},ROUTE_START_FAILED);return False
	record=json_record(path)
	if record is not None and record.get('exit')is None:
		record['pid']=child.pid;started=process_start_identity(child.pid)
		if started:record['pid_started_at']=started
		write_run_record(path,record)
	return True
def route_command(state_dir,name,argv):
	if not RUN_NAME.fullmatch(name or''):print('arena-preview route: the name takes 1 to 64 letters, digits, dot, dash or underscore.',flush=True);return 2
	if argv[:1]==['--']:argv=argv[1:]
	if not argv:print('arena-preview route: name a command after --, for example: route gates -- make check',flush=True);return 2
	if not detach_run(state_dir,name,argv):print(f"arena-preview route: the start of `{name}` failed.",flush=True);return 1
	print(f"routed {name}: the run continues in the background, and its verdict prints on a later call. {WORKTREE_STEP}",file=sys.stderr,flush=True);return 0
def run_log_report(state_dir,name):
	record=routed_record(state_dir,name)
	if record.get('exit')is None:elapsed=detached_seconds(record.get('started_at'));age=f" {elapsed}s"if elapsed is not None else'';print(f"run {name} running{age}");return 0
	print(f"run {name} done: exit {record['exit']} {record.get('seconds')}s")
	for line in record.get('tail')or[]:print(line)
	return 0
def poll_inbox(store,sleeper=None):
	if sleeper is None:sleeper=time.sleep
	listing={'checked_at':None,'pending':[]};print('POLL: 1800 s; a cut call retries with the tool timeout at 1800.',file=sys.stderr,flush=True);store.start_poll();failures=[];threading.Thread(target=pr_check_once,args=(failures,resolve_repo_root()),daemon=True).start()
	try:
		for index in range(POLL_MAX_LOOPS):
			listing=store.read(include_quiet=False)
			if listing['pending']:full=store.read();print(format_read(full),flush=True);store.mark_seen([item['id']for item in full['pending']]);store.mark_reports_agent_seen([item.get('report_id')for item in full['pending']]);return 0
			if run_notices(store.path.parent):print('CLOSING POLL REQUIRED',flush=True);return 0
			if failures:print(f"PR CHECKS FAILED: {failures[0]}. Fix the failed check before the next push.",flush=True);return 0
			open_tasks=[item for item in store.list_tasks()if item['status']=='upcoming'and not item['blocked']]
			if open_tasks:listing['tasks']=open_tasks;names=', '.join(item['id']for item in open_tasks);continue_line=f"CONTINUE: unblocked task {names}. Work it or block it; do not end the turn.";print(continue_line,flush=True);print(format_task_list(listing.get('tasks',[])),flush=True);return 0
			if skip_poll_ready(store):store.take_skip_poll();store.mark_turn_ended();print('SKIP: owner pressed Skip poll. End the turn, no second poll.',flush=True);print(format_read(listing),flush=True);return 0
			if index+1<POLL_MAX_LOOPS:sleeper(POLL_INTERVAL);store.stamp_polling()
	finally:store.clear_polling()
	store.mark_turn_ended();print('Poll ended: 0 pending.',flush=True);return 1
def parse_state_import(text):
	try:value=json.loads(text)
	except json.JSONDecodeError:value=[json.loads(line)for line in text.splitlines()if line.strip()]
	if isinstance(value,dict)and'notes'in value:
		notes=value['notes'];tasks=value.get('tasks',{})
		if tasks is None:tasks={}
		answers=value.get('submissions',[])
		if isinstance(tasks,dict):tasks=tasks.get('upcoming',[])+tasks.get('finished',[])
		if not all(isinstance(items,list)for items in(notes,tasks,answers)):raise ValueError('State collections must be arrays')
		value=notes+tasks+answers
	elif isinstance(value,dict):value=[value]
	if not isinstance(value,list)or not all(isinstance(r,dict)for r in value):raise ValueError('Import JSON records or copied state')
	if not value:raise ValueError('Nothing to import')
	return value
def task_steps(details):
	steps=[]
	for item in details or():steps.extend(part.strip()for part in str(item).splitlines())
	return[step for step in steps if step]
def check_task_steps(details):
	for step in details or():
		if len(step)>TASK_STEP_MAX:raise ValueError(f"A task detail is one step per line, {TASK_STEP_MAX} characters or fewer; this line runs {len(step)}. Split it and repeat --details")
def check_task(task_id,title,details):
	if not TASK_ID.match(task_id or''):raise ValueError('A task ID is 1-64 characters of lowercase letters, digits and hyphens, and starts with a letter or digit')
	if NOTE_ID.match(task_id or''):raise ValueError('That ID is a note ID. A task ID names the job: give the task a proper name, like fix-the-widget')
	if title is not None and len(title)>MAX_TASK_TITLE:raise ValueError(f"A task title must be {MAX_TASK_TITLE} characters or fewer")
	if len(details or())>MAX_TASK_DETAILS:raise ValueError(f"A task carries at most {MAX_TASK_DETAILS} details")
	for detail in details or():
		if len(detail)>MAX_TASK_DETAIL:raise ValueError(f"A task detail must be {MAX_TASK_DETAIL} characters or fewer")
SIZED_IMAGE=re.compile('!\\[([^\\]\\n]*)\\]\\((\\S+?)\\s+=(\\d+)x(\\d*)\\)')
def hold_out(markdown,pattern,build,slug):
	held=[]
	def hold(match):
		built=build(match)
		if built is None:return match.group(0)
		held.append(built);return f" preview{slug}{len(held)-1}x "
	return re.sub(pattern,hold,markdown),held
def put_back(rendered,held,slug):return re.sub(f"preview{slug}(\\d+)x",lambda match:held[int(match.group(1))],rendered)
def render_report_block(markdown):tagged,held=hold_out(markdown,'(?:\\s*(?:</?(?:ul|ol|li)>)\\s*){2,}',lambda match:match.group(0).strip().lower(),'listtag');out=put_back(render(tagged),held,'listtag');out=re.sub('<p>(<(?:ul|ol)>)','\\1',out);out=re.sub('(</(?:ul|ol)>)</p>','\\1',out);return out
def render_report(markdown):
	blocks,questions=parse_fields(markdown);parts=[]
	for(kind,item)in blocks:
		if kind=='markdown':
			if item.strip():parts.append(render_report_block(item))
		else:parts.append(field_html(item))
	return''.join(parts),questions
class ReportChanged(ValueError):pass
class FetchChanged(ValueError):pass
class ReportHeld(ValueError):pass
class Store:
	def __init__(self,directory,create=False,save_path=None):
		directory=Path(directory).resolve();self.path=directory/'state.sqlite3';self.save_path=Path(save_path)if save_path else self.path.parent/SAVED_STATE;existed=self.path.is_file()
		if not create and not existed:raise FileNotFoundError(f"Inbox missing: {self.path}. The sandbox may have been reset, so follow the restore routine: run scripts/install.sh from the repository root, start `arena-preview serve --port 8000` with the start_process tool, then `arena-preview read`.")
		if create and not existed:directory.mkdir(parents=True,exist_ok=True,mode=448)
		with closing(self.connect())as db,db:
			db.executescript("\n        CREATE TABLE IF NOT EXISTS notes (\n          seq INTEGER PRIMARY KEY, id TEXT UNIQUE NOT NULL,\n          text TEXT NOT NULL, at TEXT NOT NULL, acknowledged_at TEXT,\n          ack_kind TEXT, ack_text TEXT, seen_at TEXT, replies TEXT,\n          ack_edited_seen_count INTEGER NOT NULL DEFAULT 0,\n          quiet INTEGER NOT NULL DEFAULT 0\n        );\n        CREATE TABLE IF NOT EXISTS reports (\n          id TEXT PRIMARY KEY, title TEXT NOT NULL,\n          markdown TEXT NOT NULL, updated_at TEXT NOT NULL, published_at TEXT,\n          seq INTEGER, seen_at TEXT,\n          ever_seen INTEGER NOT NULL DEFAULT 0, agent_seen_at TEXT,\n          ack_seen_at TEXT, viewed_at TEXT\n        );\n        CREATE TABLE IF NOT EXISTS submissions (\n          seq INTEGER PRIMARY KEY, id TEXT UNIQUE NOT NULL,\n          report_id TEXT NOT NULL, text TEXT NOT NULL, at TEXT NOT NULL,\n          acknowledged_at TEXT, ack_kind TEXT, ack_text TEXT, seen_at TEXT, replies TEXT,\n          ack_edited_seen_count INTEGER NOT NULL DEFAULT 0\n        );\n        CREATE TABLE IF NOT EXISTS uploads (\n          seq INTEGER PRIMARY KEY, id TEXT UNIQUE NOT NULL, note_id TEXT,\n          name TEXT NOT NULL, type TEXT NOT NULL, size INTEGER NOT NULL,\n          sha256 TEXT NOT NULL, file TEXT NOT NULL, at TEXT NOT NULL\n        );\n        CREATE TABLE IF NOT EXISTS fetch_jobs (\n          seq INTEGER PRIMARY KEY, id TEXT UNIQUE NOT NULL,\n          url TEXT NOT NULL, allow_proxy INTEGER NOT NULL CHECK (allow_proxy IN (0, 1)),\n          status TEXT NOT NULL CHECK (status IN ('queued', 'fetching', 'saved', 'failed')),\n          approval TEXT NOT NULL DEFAULT 'approved'\n            CHECK (approval IN ('pending', 'approved', 'denied')),\n          claim TEXT, lease_until TEXT, error TEXT, source TEXT,\n          name TEXT, type TEXT, size INTEGER, sha256 TEXT, file TEXT,\n          at TEXT NOT NULL, updated_at TEXT NOT NULL\n        );\n        CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);\n        CREATE TABLE IF NOT EXISTS tasks (\n          id TEXT PRIMARY KEY,\n          title TEXT NOT NULL,\n          details TEXT NOT NULL DEFAULT '[]',\n          status TEXT NOT NULL DEFAULT 'upcoming'\n            CHECK (status IN ('upcoming', 'finished')),\n          position INTEGER NOT NULL,\n          created_at TEXT NOT NULL,\n          updated_at TEXT NOT NULL,\n          completed_at TEXT,\n          blocked INTEGER NOT NULL DEFAULT 0\n        );\n      ");columns={row['name']for row in db.execute('PRAGMA table_info(notes)')}
			for column in('ack_kind','ack_text','ack_edited_at','seen_at','task_id','replies'):
				if column not in columns:db.execute(f"ALTER TABLE notes ADD COLUMN {column} TEXT")
			if'ack_edited_seen_count'not in columns:db.execute('ALTER TABLE notes ADD COLUMN ack_edited_seen_count INTEGER NOT NULL DEFAULT 0')
			if'quiet'not in columns:db.execute('ALTER TABLE notes ADD COLUMN quiet INTEGER NOT NULL DEFAULT 0')
			if'origin'in columns:db.execute('ALTER TABLE notes DROP COLUMN origin');columns.discard('origin')
			if'seen_at'not in columns:db.execute('UPDATE notes SET seen_at = acknowledged_at WHERE seen_at IS NULL AND acknowledged_at IS NOT NULL')
			columns={row['name']for row in db.execute('PRAGMA table_info(tasks)')}
			if'blocked'not in columns:db.execute('ALTER TABLE tasks ADD COLUMN blocked INTEGER NOT NULL DEFAULT 0')
			if'report_id'not in columns:db.execute('ALTER TABLE tasks ADD COLUMN report_id TEXT')
			if'completed_at'not in columns:db.execute('ALTER TABLE tasks ADD COLUMN completed_at TEXT')
			db.execute("UPDATE tasks SET completed_at = updated_at WHERE status = 'finished' AND completed_at IS NULL");columns={row['name']for row in db.execute('PRAGMA table_info(submissions)')}
			if'ack_edited_at'not in columns:db.execute('ALTER TABLE submissions ADD COLUMN ack_edited_at TEXT')
			if'seen_at'not in columns:db.execute('ALTER TABLE submissions ADD COLUMN seen_at TEXT')
			if'task_id'not in columns:db.execute('ALTER TABLE submissions ADD COLUMN task_id TEXT')
			if'replies'not in columns:db.execute('ALTER TABLE submissions ADD COLUMN replies TEXT')
			if'ack_edited_seen_count'not in columns:db.execute('ALTER TABLE submissions ADD COLUMN ack_edited_seen_count INTEGER NOT NULL DEFAULT 0')
			columns={row['name']for row in db.execute('PRAGMA table_info(reports)')}
			if'seen_at'not in columns:db.execute('ALTER TABLE reports ADD COLUMN seen_at TEXT');db.execute('UPDATE submissions SET seen_at = acknowledged_at WHERE seen_at IS NULL AND acknowledged_at IS NOT NULL')
			columns={row['name']for row in db.execute('PRAGMA table_info(reports)')}
			if'seq'not in columns:db.execute('ALTER TABLE reports ADD COLUMN seq INTEGER');db.execute('UPDATE reports SET seq = rowid WHERE seq IS NULL')
			if'ever_seen'not in columns:db.execute('ALTER TABLE reports ADD COLUMN ever_seen INTEGER NOT NULL DEFAULT 0');db.execute('UPDATE reports SET ever_seen = 1 WHERE seen_at IS NOT NULL')
			if'agent_seen_at'not in columns:db.execute('ALTER TABLE reports ADD COLUMN agent_seen_at TEXT')
			if'ack_seen_at'not in columns:db.execute('ALTER TABLE reports ADD COLUMN ack_seen_at TEXT')
			if'viewed_at'not in columns:db.execute('ALTER TABLE reports ADD COLUMN viewed_at TEXT')
			columns={row['name']for row in db.execute('PRAGMA table_info(reports)')}
			if'published_at'not in columns:db.execute('ALTER TABLE reports ADD COLUMN published_at TEXT');db.execute('UPDATE reports SET published_at = updated_at WHERE published_at IS NULL')
			columns={row['name']for row in db.execute('PRAGMA table_info(uploads)')}
			if'note_id'not in columns:db.execute('ALTER TABLE uploads ADD COLUMN note_id TEXT');db.execute('UPDATE uploads SET note_id = id WHERE note_id IS NULL')
			db.execute('CREATE INDEX IF NOT EXISTS uploads_note_id ON uploads(note_id)');columns={row['name']for row in db.execute('PRAGMA table_info(fetch_jobs)')}
			if'approval'not in columns:db.execute("ALTER TABLE fetch_jobs ADD COLUMN approval TEXT NOT NULL DEFAULT 'approved' CHECK (approval IN ('pending', 'approved', 'denied'))")
			if'origin'not in columns:db.execute("ALTER TABLE fetch_jobs ADD COLUMN origin TEXT NOT NULL DEFAULT 'agent'")
		if not existed:self.path.chmod(384)
	def id_matches(self,token):
		token=bare_id(token);like=f"{token}%";found=[]
		with closing(self.connect())as db:
			for(table,label)in(('notes','note'),('submissions','answer'),('reports','report'),('tasks','task')):rows=db.execute(f"SELECT * FROM {table} WHERE id = ? OR id LIKE ?",(token,like)).fetchall();found.extend((label,dict(row))for row in rows)
		return found
	def resolve_id(self,token):
		token=bare_id(token);matches=self.id_matches(token);exact=[item for item in matches if item[1]['id']==token]
		if len(exact)==1:return token
		if len(matches)==1:return matches[0][1]['id']
		if not matches:raise ValueError(f"No note, answer, report or task holds the ID {token}")
		listed='; '.join(f"{kind} {record['id']} {(record.get('title')or record.get('text')or'')[:40]!r}"for(kind,record)in matches);raise ValueError(f"{token} matches {len(matches)} records: {listed}. Give the full ID of one")
	def list_note_ids(self,page,page_size):
		if page<1 or not 1<=page_size<=100:raise ValueError('Page must be at least 1; page size must be 1 to 100')
		with closing(self.connect())as db:
			db.execute('BEGIN');total=db.execute('SELECT count(*) FROM notes').fetchone()[0];offset=(page-1)*page_size;ids=[]
			if offset<total:ids=[row[0]for row in db.execute('SELECT id FROM notes ORDER BY at DESC, seq DESC LIMIT ? OFFSET ?',(page_size,offset))]
		return ids,total
	def notes_matching(self,token):
		with closing(self.connect())as db:return[message_row(row)for row in db.execute('SELECT * FROM notes WHERE id = ? OR id LIKE ? ORDER BY at DESC',(token,f"{token}%")).fetchall()]
	def connect(self):db=sqlite3.connect(self.path,timeout=5);db.row_factory=sqlite3.Row;return db
	def note(self,note_id,text,at=None,acknowledged_at=None,ack_kind=None,ack_text=None,seen_at=None,task_id=None,ack_edited_at=None,autosave=True,shared=None,replies=None,ack_edited_seen_count=None,quiet=False):
		identifier(note_id);owner_text(text);receipt=restore_receipt(acknowledged_at,ack_kind,ack_text,ack_edited_at);more=restore_replies(replies,receipt[0]);seen_reply_count=restore_reply_seen_count(ack_edited_seen_count,more);seen=when(seen_at)if seen_at is not None else None
		with self.transaction(shared,autosave=autosave)as db:
			if shared is None:db.execute('BEGIN IMMEDIATE')
			existing=db.execute('SELECT * FROM notes WHERE id = ?',(note_id,)).fetchone()
			if existing:
				if existing['text']!=text:raise ValueError('This message ID already belongs to different text')
				return message_row(existing)
			db.execute('INSERT INTO notes (id, text, at, acknowledged_at, ack_kind, ack_text, ack_edited_at, seen_at, task_id, replies, ack_edited_seen_count, quiet) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',(note_id,text,at or now(),*receipt,seen,task_id,more,seen_reply_count,1 if quiet else 0))
			if not quiet:reset_poll_count(db)
			clear_skip_poll(db);return message_row(db.execute('SELECT * FROM notes WHERE id = ?',(note_id,)).fetchone())
	def submission(self,submission_id,report_id,text,at=None,acknowledged_at=None,ack_kind=None,ack_text=None,seen_at=None,task_id=None,ack_edited_at=None,shared=None,autosave=True,replies=None,ack_edited_seen_count=None):
		identifier(submission_id);identifier(report_id);submission_text(text);receipt=restore_receipt(acknowledged_at,ack_kind,ack_text,ack_edited_at);more=restore_replies(replies,receipt[0]);seen_reply_count=restore_reply_seen_count(ack_edited_seen_count,more)
		with self.transaction(shared,autosave=autosave)as db:
			if shared is None:db.execute('BEGIN IMMEDIATE')
			existing=db.execute('SELECT * FROM submissions WHERE id = ?',(submission_id,)).fetchone()
			if existing:
				if existing['text']!=text:raise ValueError('This message ID already belongs to different text')
				self.release_report_tasks(db,report_id);return message_row(existing)
			db.execute('INSERT INTO submissions (id, report_id, text, at, acknowledged_at, ack_kind, ack_text, ack_edited_at, seen_at, task_id, replies, ack_edited_seen_count) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',(submission_id,report_id,text,at or now(),*receipt,when(seen_at)if seen_at is not None else None,task_id,more,seen_reply_count));self.release_report_tasks(db,report_id);reset_poll_count(db);clear_skip_poll(db);return message_row(db.execute('SELECT * FROM submissions WHERE id = ?',(submission_id,)).fetchone())
	def submissions(self):
		with closing(self.connect())as db:return[message_row(row)for row in db.execute('SELECT * FROM submissions ORDER BY at, seq')]
	def report_sources(self):
		with closing(self.connect())as db:return[dict(row)for row in db.execute('SELECT id, title, markdown, published_at, seen_at FROM reports ORDER BY seq, id')]
	def state(self):
		tasks=self.tasks()
		with closing(self.connect())as db:
			meta=dict(db.execute('SELECT key, value FROM meta'));notes=[message_row(row)for row in db.execute('SELECT * FROM notes ORDER BY at, seq')];reports=[dict(row)for row in db.execute('SELECT id, title, updated_at, published_at, seq, seen_at, ever_seen, agent_seen_at, ack_seen_at, markdown, EXISTS(SELECT 1 FROM submissions WHERE report_id = reports.id) AS answered FROM reports ORDER BY seq, id')];latest_answers={row['report_id']:row for row in db.execute('SELECT * FROM submissions ORDER BY seq')}
			for report in reports:
				answered=report.pop('answered');latest=latest_answers.get(report['id']);report['acknowledgements']=[message_row(row)for row in db.execute('SELECT * FROM submissions WHERE report_id = ? AND acknowledged_at IS NOT NULL ORDER BY seq',(report['id'],))]
				for ack in report['acknowledgements']:ack.pop('text',None)
				report['latest_answer_id']=latest['id']if latest else None;report['latest_answer_at']=clip_stamp(latest['at'])if latest else None;report['latest_answer_acknowledged_at']=clip_stamp(latest['acknowledged_at'])if latest else None;agent_seen=report.pop('agent_seen_at',None);report['agent_seen_at']=clip_stamp(agent_seen)if latest is not None and at_or_after(agent_seen,latest['at'])else None
				try:report['needs_answer']=bool(parse_fields(report.pop('markdown'))[1])and not answered
				except ValueError as error:report['needs_answer']=True;report['field_error']=str(error)
			uploads=self.uploads();by_note={}
			for item in uploads:by_note.setdefault(item['note_id'],[]).append(item)
			for note in notes:add_note_attachments(note,by_note.get(note['id'],[]))
			fetch_jobs=self.fetch_jobs()
			for item in notes+reports+uploads+fetch_jobs:
				for key in('at','acknowledged_at','ack_edited_at','seen_at','ack_seen_at','updated_at','published_at'):
					if key in item:item[key]=clip_stamp(item[key])
				for reply in item.get('replies')or[]:reply['at']=clip_stamp(reply['at'])
			if tasks is not None:
				for item in tasks['finished']+tasks['upcoming']:item['updated_at']=clip_stamp(item['updated_at'])
				tasks['updated_at']=clip_stamp(tasks['updated_at'])
			return{'notes':notes,'reports':reports,'tasks':tasks,'uploads':uploads,'fetch_jobs':fetch_jobs,'workspace':workspace_usage(),'last_check':clip_stamp(meta.get('last_check')),'polling':self.polling(),'polling_since':clip_stamp(meta.get(POLL_SINCE_META))if self.polling()else None,'skip_poll':clip_stamp(meta.get(SKIP_POLL_META)),'calls_since_message':meta_number(db,POLLS_SINCE_MESSAGE),'agent_key':self.agent_key(),'agent_seen_at':clip_stamp(meta.get(AGENT_SEEN_META)),'turn_ended_at':clip_stamp(meta.get(TURN_ENDED_META)),'agent_call_ended_at':clip_stamp(meta.get(AGENT_CALL_ENDED_META))}
	def tasks(self):
		with closing(self.connect())as db:rows=db.execute(f"SELECT {TASK_COLUMNS} FROM tasks ORDER BY {TASK_ORDER}").fetchall()
		records=[task_row(row)for row in rows]
		if not records:return None
		return{'finished':[item for item in records if item['status']=='finished'],'upcoming':[item for item in records if item['status']=='upcoming'],'updated_at':max(item['updated_at']for item in records)}
	def list_tasks(self):
		with closing(self.connect())as db:rows=db.execute(f"SELECT {TASK_COLUMNS} FROM tasks ORDER BY {TASK_ORDER}").fetchall()
		return[task_row(row)for row in rows]
	def task_record(self,task_id):
		with closing(self.connect())as db:row=db.execute(f"SELECT {TASK_COLUMNS} FROM tasks WHERE id = ?",(task_id,)).fetchone()
		return task_row(row)if row else None
	def free_id(self):
		tables='notes','submissions','uploads','reports','tasks'
		with closing(self.connect())as db:
			for _ in range(MINT_ATTEMPTS):
				candidate=new_id()
				if not any(db.execute(f"SELECT 1 FROM {table} WHERE id = ?",(candidate,)).fetchone()for table in tables):return candidate
		raise RuntimeError('The state holds no free six-character ID for a new record')
	@contextmanager
	def transaction(self,db=None,autosave=True):
		if db is not None:yield db;return
		with closing(self.connect())as own,own:yield own
		if autosave:self.autosave()
	def write_task(self,task_id,title=None,details=None,status=None,order=None,blocked=None,report_id=None,completed_at=None,shared=None):
		details=[str(item)for item in details if str(item).strip()]if details else None;check_task(task_id,title,details)
		if status is not None and status not in TASK_STATUSES:raise ValueError(f"A task is either {' or '.join(TASK_STATUSES)}")
		stamp=now()
		with self.transaction(shared)as db:
			row=db.execute(f"SELECT {TASK_COLUMNS} FROM tasks WHERE id = ?",(task_id,)).fetchone();stored=task_row(row)if row else None
			if stored is None and title is None:raise ValueError('A new task needs a title')
			if stored is None:self.refuse_shared_id(db,'tasks','reports',task_id)
			title=title if title is not None else stored['title']
			if details is None:details=stored['details']if stored else[]
			if not details:raise ValueError('A task needs at least one detail')
			status=status or(stored['status']if stored else'upcoming')
			if status=='finished':
				if stored and stored['status']=='finished':completed_at=stored['completed_at']
				elif completed_at is None:completed_at=stamp
			else:completed_at=None
			if blocked is None:blocked=stored['blocked']if stored else False
			if report_id is None:report_id=stored.get('report_id')if stored else None
			if report_id:identifier(report_id)
			if report_id and blocked and self.report_has_answers(db,report_id):blocked=False
			if status=='finished'and blocked:raise ValueError(f"Task {task_id} is blocked; clear the mark with --unblocked first")
			siblings=[task_row(item)for item in db.execute(f"SELECT {TASK_COLUMNS} FROM tasks WHERE status = ? AND id != ? ORDER BY position, id",(status,task_id)).fetchall()];record={'id':task_id,'title':title,'details':details,'status':status,'order':len(siblings)+1,'updated_at':stamp,'completed_at':completed_at,'blocked':bool(blocked),'report_id':report_id}
			if order is not None:index=max(0,min(order-1,len(siblings)))
			elif stored and stored['status']==status:index=max(0,min(stored['order']-1,len(siblings)))
			else:index=len(siblings)
			siblings.insert(index,record);record['order']=index+1;db.execute('INSERT INTO tasks (id, title, details, status, position, created_at, updated_at, completed_at, blocked, report_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET title = excluded.title, details = excluded.details, status = excluded.status, position = excluded.position, updated_at = excluded.updated_at, completed_at = excluded.completed_at, blocked = excluded.blocked, report_id = excluded.report_id',(task_id,title,json.dumps(details,ensure_ascii=False),status,index+1,stamp,stamp,completed_at,1 if blocked else 0,report_id))
			for(position,item)in enumerate(siblings,1):db.execute('UPDATE tasks SET position = ? WHERE id = ?',(position,item['id']))
			self.renumber(db)
		return record
	@staticmethod
	def report_has_answers(db,report_id):return bool(db.execute('SELECT 1 FROM submissions WHERE report_id = ? LIMIT 1',(report_id,)).fetchone())
	@staticmethod
	def release_report_tasks(db,report_id):db.execute('UPDATE tasks SET blocked = 0, updated_at = ? WHERE report_id = ? AND blocked = 1',(now(),report_id))
	def remove_task(self,task_id):
		with self.transaction()as db:
			row=db.execute(f"SELECT {TASK_COLUMNS} FROM tasks WHERE id = ?",(task_id,)).fetchone()
			if row is None:raise ValueError(f"No task is stored under {task_id}")
			db.execute('DELETE FROM tasks WHERE id = ?',(task_id,));self.renumber(db)
		return task_row(row)
	@staticmethod
	def refuse_shared_id(db,table,other,identity):
		if db.execute(f"SELECT 1 FROM {other} WHERE id = ?",(identity,)).fetchone():raise ValueError(f"{identity} already names a {other[:-1]}; a {table[:-1]} needs another ID, for example {identity}-{table[:-1]}")
	def rename_entity(self,prev_id,new_id):
		identifier(new_id);exact=[item for item in self.id_matches(prev_id)if item[1]['id']==prev_id]
		if len(exact)!=1:raise ValueError(f"No note, answer, report or task holds the ID {prev_id}")
		kind=exact[0][0];table,dependents=RENAME_TARGETS[kind]
		if kind=='task':check_task(new_id,None,None)
		with self.transaction()as db:
			for other in RENAME_TABLES:
				if db.execute(f"SELECT 1 FROM {other} WHERE id = ?",(new_id,)).fetchone():raise ValueError(f"{new_id} already names a row in {other}")
			db.execute(f"UPDATE {table} SET id = ? WHERE id = ?",(new_id,prev_id))
			if table=='tasks':db.execute('UPDATE tasks SET updated_at = ? WHERE id = ?',(now(),new_id))
			for(other,column)in dependents:db.execute(f"UPDATE {other} SET {column} = ? WHERE {column} = ?",(new_id,prev_id))
		return kind
	def amend_task(self,prev_id,task_id):
		check_task(task_id,None,None);stamp=now()
		with self.transaction()as db:
			row=db.execute('SELECT id, title, details, status, position, created_at, completed_at, blocked, report_id FROM tasks WHERE id = ?',(prev_id,)).fetchone()
			if row is None:raise ValueError(f"No task is stored under {prev_id}")
			if db.execute('SELECT 1 FROM tasks WHERE id = ?',(task_id,)).fetchone():raise ValueError(f"A task is already stored under {task_id}")
			self.refuse_shared_id(db,'tasks','reports',task_id);db.execute('DELETE FROM tasks WHERE id = ?',(prev_id,));db.execute('INSERT INTO tasks (id, title, details, status, position, created_at, updated_at, completed_at, blocked, report_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',(task_id,row[1],row[2],row[3],row[4],row[5],stamp,row[6],row[7],row[8]));self.renumber(db)
	def autosave(self):
		try:self.save_state({'notes':self.state()['notes'],'tasks':self.tasks()})
		except Exception as error:print(f"preview: autosave failed: {error}",file=sys.stderr)
	def state_lines(self,notes,tasks):
		if not isinstance(notes,list)or not isinstance(tasks,dict):raise TypeError('Save a state object with notes and tasks')
		saved_notes=[record for record in notes if not record.get('quiet')];lines=[saved_note_line(record)for record in saved_notes]
		for status in TASK_STATUSES:
			for record in tasks.get(status)or[]:lines.append(saved_task_line(record))
		sources=self.report_sources();live={record['id']for record in sources};answers=[saved_answer_line(record)for record in self.submissions()if record.get('report_id')in live];lines.extend(answers);reports=[saved_report_line(record)for record in sources];lines.extend(reports);return lines,{'notes':len(saved_notes),'tasks':len(lines)-len(saved_notes)-len(answers)-len(reports),'answers':len(answers),'reports':len(reports)}
	def newest_stamps(self):
		groups=('note','notes',('at','acknowledged_at','ack_edited_at','seen_at')),('answer','submissions',('at','acknowledged_at','seen_at')),('report','reports',('updated_at','published_at','seen_at','agent_seen_at')),('upload','uploads',('at',)),('task','tasks',('updated_at',));stamps={}
		with closing(self.connect())as db:
			for(group,table,names)in groups:
				exprs=', '.join(f"MAX({name})"for name in names);newest=None
				for value in db.execute(f"SELECT {exprs} FROM {table}").fetchone():
					stamp=import_stamp(value)
					if stamp and(newest is None or stamp>newest):newest=stamp
				stamps[group]=newest.isoformat()if newest else None
			removed=db.execute('SELECT value FROM meta WHERE key = ?',(REMOVED_META,)).fetchone();mark=import_stamp(removed[0])if removed else None;stamps['removed']=mark.isoformat()if mark else None
		return stamps
	def newest_stamp(self,stamps=None):stamps=stamps or self.newest_stamps();values=[import_stamp(value)for value in stamps.values()];values=[value for value in values if value];return max(values).isoformat()if values else None
	def save_state(self,payload):
		if not isinstance(payload,dict):raise TypeError('Save a state object')
		notes=payload.get('notes');tasks=payload.get('tasks')
		if tasks is None:tasks={}
		lines,counts=self.state_lines(notes,tasks);path=self.save_path
		if path.parent!=Path('.'):path.parent.mkdir(parents=True,exist_ok=True)
		path.write_text(state_ndjson(lines),encoding='utf-8');return{'path':str(path),**counts}
	def uploads(self):
		with closing(self.connect())as db:rows=db.execute('SELECT * FROM uploads ORDER BY seq').fetchall()
		return[upload_row(row,self.path.parent)for row in rows]
	def save_upload(self,name,content_type,data,upload_id=None,shared=None,note_id=None,position=None):
		if not isinstance(data,(bytes,bytearray,FileBody)):raise TypeError('An upload is bytes')
		if not data:raise ValueError('An upload must not be empty')
		cleaned=upload_name(name);kind=upload_type(content_type);digest=body_digest(data);upload_id=identifier(upload_id)if upload_id is not None else self.free_id();note_id=identifier(note_id)if note_id is not None else upload_id
		if position is not None and(not isinstance(position,int)or position<1):raise ValueError('Invalid attachment position')
		directory=self.path.parent/UPLOAD_DIR;directory.mkdir(parents=True,exist_ok=True,mode=448)
		with self.transaction(shared)as db:
			if shared is None:db.execute('BEGIN IMMEDIATE')
			existing=db.execute('SELECT * FROM uploads WHERE id = ?',(upload_id,)).fetchone()
			if existing:
				if(existing['note_id'],existing['name'],existing['type'],existing['size'],existing['sha256'])!=(note_id,cleaned,kind,len(data),digest):raise ValueError('This note ID already belongs to a different file')
				target=directory/existing['file']
				if not target.is_file()or hashlib.sha256(target.read_bytes()).hexdigest()!=digest:save_body(target,data);target.chmod(384)
				return upload_row(existing,self.path.parent)
			duplicate_count=db.execute("SELECT COUNT(*) FROM uploads WHERE note_id = ? AND replace(name, ' ', '-') = ?",(note_id,cleaned.replace(' ','-'))).fetchone()[0];number=duplicate_count+1;timestamp=int(time.time());extension=Path(cleaned).suffix[:16].replace(' ','-');filename_stem=Path(cleaned).stem.replace(' ','-')
			while True:
				duplicate=f"-{number}"if number>1 else'';stem=f"{note_id[:6]}-{timestamp}{duplicate}-{filename_stem}";target=directory/f"{stem}{extension}"
				if len(os.fsencode(target.name))>255:target=directory/f"{upload_id}{duplicate}{extension}"
				collision=db.execute('SELECT 1 FROM uploads WHERE file = ?',(target.name,)).fetchone()
				if not collision and not target.exists():break
				number+=1
			save_body(target,data);target.chmod(384);db.execute('INSERT INTO uploads (id, note_id, name, type, size, sha256, file, at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)',(upload_id,note_id,cleaned,kind,len(data),digest,target.name,now()));row=db.execute('SELECT * FROM uploads WHERE id = ?',(upload_id,)).fetchone()
		return upload_row(row,self.path.parent)
	def note_with_uploads(self,note_id,text,files):
		identifier(note_id);owner_text(text)
		if not isinstance(files,(list,tuple))or len(files)<1:raise ValueError('Attach at least one file to a note')
		prepared=[]
		for file in files:
			if not isinstance(file,(list,tuple))or len(file)!=3:raise ValueError('Each attachment needs a name, type and bytes')
			name,content_type,data=file
			if not isinstance(data,(bytes,bytearray,FileBody)):raise TypeError('An upload is bytes')
			if not data:raise ValueError('An upload must not be empty')
			prepared.append((upload_name(name),upload_type(content_type),data if isinstance(data,FileBody)else bytes(data)))
		created=[]
		try:
			with self.transaction(autosave=False)as db:
				db.execute('BEGIN IMMEDIATE');existing=db.execute('SELECT text FROM notes WHERE id = ?',(note_id,)).fetchone();rows=db.execute('SELECT * FROM uploads WHERE note_id = ? ORDER BY seq',(note_id,)).fetchall()
				if existing and existing['text']!=text:raise ValueError('This message ID already belongs to different text')
				if rows:
					if len(rows)!=len(prepared)or any((row['name'],row['type'],row['size'],row['sha256'])!=(name,kind,len(data),body_digest(data))for(row,(name,kind,data))in zip(rows,prepared)):raise ValueError('This note ID already belongs to different files')
					for(row,(_,_,data))in zip(rows,prepared):
						target=self.path.parent/UPLOAD_DIR/row['file']
						if not target.is_file()or hashlib.sha256(target.read_bytes()).hexdigest()!=row['sha256']:target.parent.mkdir(parents=True,exist_ok=True,mode=448);save_body(target,data);target.chmod(384)
					records=[upload_row(row,self.path.parent)for row in rows]
				else:
					if existing:raise ValueError('This note ID was already sent without a file')
					records=[]
					for(index,(name,kind,data))in enumerate(prepared,1):record=self.save_upload(name,kind,data,upload_id=note_id if index==1 else new_id(),note_id=note_id,position=index if len(prepared)>1 else None,shared=db);records.append(record);created.append(Path(record['path']))
				note=self.note(note_id,text,shared=db);result=add_note_attachments(note,records)
		except Exception:
			for path in created:path.unlink(missing_ok=True)
			raise
		self.autosave();return result
	def fetch_jobs(self):
		with closing(self.connect())as db:rows=db.execute('SELECT * FROM fetch_jobs ORDER BY seq DESC').fetchall()
		return[fetch_row(row,self.path.parent)for row in rows]
	def enqueue_fetch(self,url,allow_proxy,*,pending=False):
		url=fetch_url(url,agent=pending)
		if not isinstance(allow_proxy,bool):raise TypeError('Proxy fallback must be true or false for this URL')
		if not isinstance(pending,bool):raise TypeError('Pending approval must be true or false')
		job_id,stamp=new_id(),now()
		with self.transaction(autosave=False)as db:db.execute("INSERT INTO fetch_jobs (id, url, allow_proxy, status, approval, at, updated_at, origin) VALUES (?, ?, ?, 'queued', ?, ?, ?, ?)",(job_id,url,int(allow_proxy),'pending'if pending else'approved',stamp,stamp,'agent'if pending else'owner'));row=db.execute('SELECT * FROM fetch_jobs WHERE id = ?',(job_id,)).fetchone()
		return fetch_row(row,self.path.parent)
	def decide_fetch(self,job_id,decision):
		identifier(job_id)
		if decision not in{'approved','denied'}:raise ValueError('Choose Approve or Deny for the download')
		with self.transaction(autosave=False)as db:
			changed=db.execute("UPDATE fetch_jobs SET approval = ?, status = CASE WHEN ? = 'denied' THEN 'failed' ELSE status END, error = CASE WHEN ? = 'denied' THEN 'Denied in the preview' ELSE NULL END, updated_at = ? WHERE id = ? AND approval = 'pending' AND status = 'queued'",(decision,decision,decision,now(),job_id))
			if changed.rowcount!=1:
				if db.execute('SELECT 1 FROM fetch_jobs WHERE id = ?',(job_id,)).fetchone()is None:raise FileNotFoundError('No queued download with that ID')
				raise FetchChanged('This download request was already decided; refresh Downloads')
			row=db.execute('SELECT * FROM fetch_jobs WHERE id = ?',(job_id,)).fetchone()
		return fetch_row(row,self.path.parent)
	def claim_fetch(self):
		stamp=now()
		with self.transaction(autosave=False)as db:
			db.execute("UPDATE fetch_jobs SET status = 'queued', claim = NULL, lease_until = NULL, error = 'Browser stopped; queued again', updated_at = ? WHERE status = 'fetching' AND approval = 'approved' AND lease_until <= ?",(stamp,stamp));row=db.execute("SELECT id FROM fetch_jobs WHERE status = 'queued' AND approval = 'approved' ORDER BY seq LIMIT 1").fetchone()
			if row is None:return None
			job_id=row['id'];claim=secrets.token_urlsafe(24);lease=(datetime.now(timezone.utc)+FETCH_LEASE).isoformat();db.execute("UPDATE fetch_jobs SET status = 'fetching', claim = ?, lease_until = ?, error = NULL, updated_at = ? WHERE id = ?",(claim,lease,stamp,job_id));row=db.execute('SELECT * FROM fetch_jobs WHERE id = ?',(job_id,)).fetchone()
		return fetch_row(row,self.path.parent)|{'claim':claim}
	def claimed_fetch(self,db,job_id,claim):
		identifier(job_id);row=db.execute('SELECT * FROM fetch_jobs WHERE id = ?',(job_id,)).fetchone()
		if row is None:raise FileNotFoundError('No queued download with that ID')
		if row['status']!='fetching'or row['approval']!='approved'or not isinstance(claim,str)or not secrets.compare_digest(row['claim']or'',claim)or not row['lease_until']or row['lease_until']<=now():raise FetchChanged('Download claim expired; refresh the queue and try again')
		return row
	def renew_fetch(self,job_id,claim):
		with self.transaction(autosave=False)as db:self.claimed_fetch(db,job_id,claim);lease=(datetime.now(timezone.utc)+FETCH_LEASE).isoformat();db.execute('UPDATE fetch_jobs SET lease_until = ? WHERE id = ?',(lease,job_id));row=db.execute('SELECT * FROM fetch_jobs WHERE id = ?',(job_id,)).fetchone()
		return fetch_row(row,self.path.parent)
	def fail_fetch(self,job_id,claim,error):
		message=str(error).strip()[:1000]or'The browser could not fetch this URL'
		with self.transaction(autosave=False)as db:self.claimed_fetch(db,job_id,claim);db.execute("UPDATE fetch_jobs SET status = 'failed', error = ?, claim = NULL, lease_until = NULL, updated_at = ? WHERE id = ?",(message,now(),job_id));row=db.execute('SELECT * FROM fetch_jobs WHERE id = ?',(job_id,)).fetchone()
		return fetch_row(row,self.path.parent)
	def drop_fetch(self,job_id):
		identifier(job_id)
		with self.transaction(autosave=False)as db:
			row=db.execute('SELECT * FROM fetch_jobs WHERE id = ?',(job_id,)).fetchone()
			if row is None:raise FileNotFoundError('No download with that ID')
			if row['status']in{'queued','fetching'}and row['claim']:raise FetchChanged('This download is active in a browser')
			db.execute('DELETE FROM fetch_jobs WHERE id = ?',(job_id,))
			if row['file']:
				target=self.path.parent/'downloads'/row['file']
				try:target.unlink()
				except FileNotFoundError:pass
		return{'dropped':job_id}
	def retry_fetch(self,job_id):
		identifier(job_id)
		with self.transaction(autosave=False)as db:
			row=db.execute('SELECT * FROM fetch_jobs WHERE id = ?',(job_id,)).fetchone()
			if row is None:raise FileNotFoundError('No queued download with that ID')
			if row['approval']!='approved':raise FetchChanged('Only approved downloads can be retried')
			if row['status']not in{'queued','failed'}:raise FetchChanged('Only a failed download can be queued again')
			db.execute("UPDATE fetch_jobs SET status = 'queued', error = NULL, updated_at = ? WHERE id = ?",(now(),job_id));row=db.execute('SELECT * FROM fetch_jobs WHERE id = ?',(job_id,)).fetchone()
		return fetch_row(row,self.path.parent)
	def complete_fetch(self,job_id,claim,name,content_type,source,data):
		if not data:raise ValueError('A download must not be empty')
		if source not in{'direct','allorigins','codetabs'}:raise ValueError('Unknown download source')
		cleaned=upload_name(name);file_type=upload_type(content_type);target=None
		try:
			with self.transaction(autosave=False)as db:
				row=self.claimed_fetch(db,job_id,claim)
				if row['origin']!='owner'and len(data)>MAX_FETCH:raise ValueError(f"A download must be 1–{MAX_FETCH:,} bytes")
				if source!='direct'and not row['allow_proxy']:raise ValueError('Proxy fallback was not enabled for this URL')
				directory=self.path.parent/FETCH_DIR;directory.mkdir(parents=True,exist_ok=True,mode=448);target=directory/f"{job_id}{Path(cleaned).suffix[:16]}";save_body(target,data);target.chmod(384);stamp=now();db.execute("UPDATE fetch_jobs SET status = 'saved', source = ?, name = ?, type = ?, size = ?, sha256 = ?, file = ?, error = NULL, claim = NULL, lease_until = NULL, updated_at = ? WHERE id = ?",(source,cleaned,file_type,len(data),body_digest(data),target.name,stamp,job_id));message=f"Download: {cleaned} ({len(data)} B, {file_type}) from {urlsplit(row['url']).hostname} via {source} saved to {target}";db.execute('INSERT INTO notes (id, text, at) VALUES (?, ?, ?)',(job_id,message,stamp));result=db.execute('SELECT * FROM fetch_jobs WHERE id = ?',(job_id,)).fetchone()
		except Exception:
			if target is not None:target.unlink(missing_ok=True)
			raise
		self.autosave();return fetch_row(result,self.path.parent)
	def import_state(self,text,replace_tasks=False,merge=False):
		records=parse_state_import(text);reports=[r for r in records if'markdown'in r and'text'not in r];tasks=[r for r in records if'markdown'not in r and'title'in r and'text'not in r];messages=[r for r in records if'markdown'not in r and('title'not in r or'text'in r)]
		with self.transaction(autosave=False)as db:
			db.execute('BEGIN IMMEDIATE')
			if not merge:
				for table in('notes','reports','submissions','tasks'):db.execute(f"DELETE FROM {table}")
			self.import_reports(reports,shared=db);self.import_tasks(tasks,replace_tasks,autosave=False,shared=db)
			for record in messages:
				if'text'not in record or'title'in record:raise ValueError('Each record must be a note, task or report answer')
				options={key:record.get(key)for key in('acknowledged_at','ack_kind','ack_text','ack_edited_at','seen_at','task_id','replies','ack_edited_seen_count')};args=[record['id']];writer=self.note
				if'report_id'in record:writer=self.submission;args.append(record['report_id'])
				writer(*args,record['text'],record.get('at'),shared=db,autosave=False,**options)
		receipt={'notes':sum('report_id'not in r for r in messages),'answers':sum('report_id'in r for r in messages),'reports':len(reports),'tasks':len(tasks)};return receipt
	def import_reports(self,records,shared=None):
		if not isinstance(records,list):raise TypeError('Import a list of report objects')
		prepared=[]
		for record in records:
			if not isinstance(record,dict):raise TypeError('Import a list of report objects')
			report_id=record.get('id');identifier(report_id);title=record.get('title')
			if not isinstance(title,str)or not title.strip()or len(title)>200:raise ValueError('Report title must contain 1–200 characters')
			markdown=record.get('markdown')
			if not isinstance(markdown,str)or not markdown.strip():raise ValueError('A saved report needs its markdown')
			if len(markdown.encode('utf-8'))>MAX_REPORT:raise ValueError('Report exceeds the 2 MB limit; split it into reports')
			published_at=record.get('published_at')
			if published_at is not None and not isinstance(published_at,str):raise ValueError('A saved publish stamp is text')
			seen_at=record.get('seen_at')
			if seen_at is not None and not isinstance(seen_at,str):raise ValueError('A saved seen stamp is text')
			if seen_at is not None:when(seen_at)
			prepared.append((report_id,title,markdown,published_at,seen_at,seen_at is not None))
		with self.transaction(shared)as db:
			for(report_id,title,markdown,published_at,seen_at,ever_seen)in prepared:highest=db.execute('SELECT COALESCE(MAX(seq), 0) FROM reports').fetchone()[0];db.execute('INSERT INTO reports\n             (id, title, markdown, updated_at, published_at, seq, seen_at, ever_seen)\n             VALUES (?, ?, ?, ?, ?, ?, ?, ?)\n             ON CONFLICT(id) DO UPDATE SET title = excluded.title,\n               markdown = excluded.markdown, updated_at = excluded.updated_at,\n               published_at = COALESCE(reports.published_at, excluded.published_at),\n               seen_at = COALESCE(reports.seen_at, excluded.seen_at),\n               ever_seen = MAX(reports.ever_seen, excluded.ever_seen)',(report_id,title,markdown,now(),published_at,highest+1,seen_at,ever_seen))
		return len(prepared)
	def import_tasks(self,records,replace=False,autosave=True,shared=None):
		if not isinstance(records,list):raise TypeError('Import a list of task objects')
		prepared=[]
		for(index,record)in enumerate(records,1):
			if not isinstance(record,dict):raise TypeError('Import a list of task objects')
			details=[str(item)for item in record.get('details')or[]if str(item).strip()];status=record.get('status');completed_at=record.get('completed_at');check_task(record.get('id'),record.get('title'),details)
			if status is not None and status not in TASK_STATUSES:raise ValueError(f"A task is either {' or '.join(TASK_STATUSES)}")
			if completed_at is not None:
				if not isinstance(completed_at,str):raise ValueError('A saved completion stamp is text')
				when(completed_at)
			prepared.append((record.get('id'),record.get('title'),details,status,record.get('order')or index,record.get('blocked'),record.get('report_id'),completed_at))
		with self.transaction(shared,autosave=autosave)as db:
			if replace:
				for(task_id,title,*_)in prepared:
					stored=db.execute('SELECT 1 FROM tasks WHERE id = ?',(task_id,)).fetchone()
					if title is None and not stored:raise ValueError(f"A new task needs a title: {task_id}")
				db.execute('DELETE FROM tasks')
			written=[self.write_task(*item,shared=db)for item in prepared]
		return written
	def renumber(self,db):
		for status in TASK_STATUSES:
			rows=db.execute('SELECT id FROM tasks WHERE status = ? ORDER BY position, id',(status,)).fetchall()
			for(position,row)in enumerate(rows,1):db.execute('UPDATE tasks SET position = ? WHERE id = ?',(position,row[0]))
	def meta_value(self,key):
		with closing(self.connect())as db:row=db.execute('SELECT value FROM meta WHERE key = ?',(key,)).fetchone();return row[0]if row else None
	def stamp_polling(self):
		with closing(self.connect())as db,db:db.execute('INSERT OR REPLACE INTO meta VALUES (?, ?)',(POLLING_META,now()))
	def start_poll(self):
		with closing(self.connect())as db,db:stamp=now();db.execute('INSERT OR REPLACE INTO meta VALUES (?, ?)',(POLLING_META,stamp));db.execute('INSERT OR REPLACE INTO meta VALUES (?, ?)',(POLL_SINCE_META,stamp))
	def clear_polling(self):
		with closing(self.connect())as db,db:db.execute('DELETE FROM meta WHERE key IN (?, ?)',(POLLING_META,POLL_SINCE_META))
	def polling(self):age=seconds_since(self.meta_value(POLLING_META));return age is not None and 0<=age<POLLING_FRESH_SECONDS
	def request_skip_poll(self,armed=True):
		if not armed:self.take_skip_poll();return{'skip_poll':None}
		self.set_meta(SKIP_POLL_META,now());return{'skip_poll':clip_stamp(self.meta_value(SKIP_POLL_META))}
	def skip_poll_requested(self):return self.meta_value(SKIP_POLL_META)is not None
	def take_skip_poll(self):
		stamp=self.meta_value(SKIP_POLL_META)
		if stamp is None:return None
		with closing(self.connect())as db,db:db.execute('DELETE FROM meta WHERE key = ?',(SKIP_POLL_META,))
		return clip_stamp(stamp)
	def set_meta(self,key,value):
		with closing(self.connect())as db,db:db.execute('INSERT OR REPLACE INTO meta VALUES (?, ?)',(key,str(value)))
	def touch_agent(self):self.set_meta(AGENT_SEEN_META,now());self.clear_turn_ended()
	def mark_turn_ended(self):self.set_meta(TURN_ENDED_META,now())
	def clear_turn_ended(self):
		with closing(self.connect())as db,db:db.execute('DELETE FROM meta WHERE key = ?',(TURN_ENDED_META,))
	def set_agent_key(self,key,host=None):self.set_meta(AGENT_KEY_META,json.dumps({'key':key,'host':host,'at':now()},ensure_ascii=False))
	def notice_expired_key(self):
		record=self.agent_key()
		if not record:return None
		stamp=import_stamp(record['at'])
		if stamp is None:return None
		if(datetime.now(timezone.utc)-stamp).total_seconds()<AGENT_KEY_EXPIRY_SECONDS:return None
		if self.meta_value(AGENT_KEY_EXPIRY_META)==record['at']:return None
		self.set_meta(AGENT_KEY_EXPIRY_META,record['at']);return self.note(self.free_id(),f"The recorded agent key expired (set {record['at']}). Run `arena-preview key` for the live key; a new post overwrites this record by itself.",quiet=True)
	def agent_key(self):
		value=self.meta_value(AGENT_KEY_META)
		if not value:return None
		try:record=json.loads(value)
		except json.JSONDecodeError:return None
		if not isinstance(record,dict):return None
		key=record.get('key')
		if not isinstance(key,str)or not AGENT_KEY_RE.fullmatch(key):return None
		host=record.get('host')
		if not isinstance(host,str)or not AGENT_HOST_RE.fullmatch(host):host=None
		return{'key':key,'host':host,'at':clip_stamp(record.get('at'))}
	def stale_report_rows(self,db):
		stale=[];rows=db.execute('SELECT id, markdown FROM reports WHERE published_at IS NOT NULL ORDER BY seq, id')
		for row in rows:
			try:fields=len(parse_fields(row['markdown'])[1])
			except ValueError:continue
			if fields==0:stale.append({'id':row['id'],'reason':'no fields'});continue
			pending=db.execute('SELECT count(*) FROM submissions WHERE report_id = ? AND acknowledged_at IS NULL',(row['id'],)).fetchone()[0];answered=db.execute('SELECT count(*) FROM submissions WHERE report_id = ?',(row['id'],)).fetchone()[0]
			if answered and not pending:stale.append({'id':row['id'],'reason':'all submissions acknowledged'})
		return stale
	def stale_report_ids(self,db):return[record['id']for record in self.stale_report_rows(db)]
	def stale_reports(self):
		with closing(self.connect())as db:return self.stale_report_rows(db)
	def reminder(self,advance=False):
		with closing(self.connect())as db,db:
			uploads=db.execute('SELECT count(*) FROM notes JOIN uploads USING (id) WHERE acknowledged_at IS NULL AND quiet = 0').fetchone()[0];notes=db.execute('SELECT count(*) FROM notes WHERE acknowledged_at IS NULL AND quiet = 0').fetchone()[0]-uploads;reports=db.execute('SELECT count(*) FROM submissions WHERE acknowledged_at IS NULL').fetchone()[0];remaining=db.execute("SELECT count(*) FROM tasks WHERE status <> 'finished'").fetchone()[0];published=db.execute('SELECT count(*) FROM reports WHERE published_at IS NOT NULL').fetchone()[0];stale=self.stale_report_ids(db)if published>=STALE_REPORT_THRESHOLD else[];cursor=meta_number(db,REMINDER_CURSOR);polls=0 if advance and not notes+reports+uploads else meta_number(db,POLLS_SINCE_MESSAGE)+(1 if advance else 0);blocked=bool(gate_pending_count(db))and polls>=GATE_THRESHOLD;db.execute('INSERT OR REPLACE INTO meta VALUES (?, ?)',(REMINDER_CURSOR,str(cursor+1)))
			if advance:db.execute('INSERT OR REPLACE INTO meta VALUES (?, ?)',(POLLS_SINCE_MESSAGE,str(polls)))
		counts=[f"{count} {kind}{'s'if count!=1 else''}"for(count,kind)in((notes,'note'),(reports,'answer'),(uploads,'upload'))if count];unacked=[f"unacked {', '.join(counts)}"]if counts else[];head=['blocked']if blocked else[f"calls {polls}"]if polls and counts else[];tail=reminder_tail(cursor,remaining,len(stale));parts=[' '.join([*head,*unacked]).strip(),tail];return' | '.join(parts).strip(' |')
	def calls_since_message(self):
		with closing(self.connect())as db:return meta_number(db,POLLS_SINCE_MESSAGE)
	def gate(self,threshold=GATE_THRESHOLD,pending_only=False):
		with closing(self.connect())as db,db:pending=gate_pending_count(db);polls=meta_number(db,POLLS_SINCE_MESSAGE)
		if pending_only:return not pending
		return not(pending and polls>=threshold)
	def read(self,include_quiet=True):
		quiet_filter=''if include_quiet else' AND quiet = 0'
		with self.transaction(autosave=False)as db:
			pending=[message_row(row)|{'kind':'note'}for row in db.execute(f"SELECT * FROM notes WHERE acknowledged_at IS NULL{quiet_filter} ORDER BY at, seq")];pending+=[message_row(row)|{'kind':'report'}for row in db.execute('SELECT * FROM submissions WHERE acknowledged_at IS NULL ORDER BY at, seq')];notes={}
			for row in db.execute('SELECT * FROM notes ORDER BY at, seq'):record=message_row(row);notes[record['id']]=record
			pending_note_ids={row['id']for row in db.execute('SELECT id FROM notes WHERE acknowledged_at IS NULL')};attachments={}
			for row in db.execute('SELECT * FROM uploads ORDER BY seq'):record=upload_row(row,self.path.parent);attachments.setdefault(record['note_id'],[]).append(record)
			for record in notes.values():add_note_attachments(record,attachments.get(record['id'],[]))
			for item in pending:
				if item['kind']=='note':
					add_note_attachments(item,attachments.get(item['id'],[]));thread=reply_thread(item,notes,pending_note_ids)
					if thread:item['thread']=thread
				for key in('at','acknowledged_at','ack_edited_at','seen_at'):item[key]=clip_stamp(item[key])
			pending.sort(key=lambda item:item['at']);checked=now();db.execute("INSERT OR REPLACE INTO meta VALUES ('last_check', ?)",(checked,));waiting=db.execute('SELECT value FROM meta WHERE key = ?',(SKIP_POLL_META,)).fetchone();return{'checked_at':clip_stamp(checked),'pending':pending,'skip_poll':clip_stamp(waiting[0])if waiting else None}
	def mark_seen(self,ids):
		stamp=now()
		with self.transaction()as db:
			for record_id in ids:
				identifier(record_id)
				for table in('notes','submissions'):
					cursor=db.execute(f"UPDATE {table} SET seen_at = COALESCE(seen_at, ?) WHERE id = ?",(stamp,record_id))
					if cursor.rowcount:break
				else:raise ValueError(f"Unknown note: {record_id}; no Seen receipts written")
	def mark_reports_agent_seen(self,report_ids):
		stamp=now()
		with self.transaction()as db:
			for report_id in report_ids or[]:
				if not report_id:continue
				identifier(report_id);db.execute('UPDATE reports SET agent_seen_at = ? WHERE id = ?',(stamp,report_id))
	def mark_replies_seen(self,record_id,count):
		identifier(record_id)
		if type(count)is not int or count<0:raise ValueError('A viewed-reply count is a nonnegative integer')
		with self.transaction()as db:
			for table in('notes','submissions'):
				row=db.execute(f"SELECT replies, ack_edited_seen_count FROM {table} WHERE id = ?",(record_id,)).fetchone()
				if row is None:continue
				reply_count=len(replies_list(row['replies']))
				if count>reply_count:raise ValueError('The viewed-reply count exceeds the current replies')
				seen_count=max(int(row['ack_edited_seen_count']or 0),count);db.execute(f"UPDATE {table} SET ack_edited_seen_count = ? WHERE id = ?",(seen_count,record_id));return{'id':record_id,'ack_edited_seen_count':seen_count}
		raise FileNotFoundError('Message not found')
	def mark_task(self,record_id,task_id,shared=None):
		identifier(record_id);identifier(task_id)
		with self.transaction(shared)as db:
			for table in('notes','submissions'):
				cursor=db.execute(f"UPDATE {table} SET task_id = ? WHERE id = ?",(task_id,record_id))
				if cursor.rowcount:return
		raise ValueError(f"Unknown note: {record_id}; no task marker written")
	def pending_ack(self):
		with self.connect()as db:rows=db.execute('SELECT id, at FROM notes WHERE acknowledged_at IS NULL AND quiet = 0 UNION ALL SELECT id, at FROM submissions WHERE acknowledged_at IS NULL ORDER BY at').fetchall()
		if not rows:raise ValueError('No pending note or answer to acknowledge')
		if len(rows)>1:listed=', '.join(row['id']for row in rows);raise ValueError(f"{len(rows)} items are pending: {listed}. Name the ID to answer one")
		return rows[0]['id']
	def acknowledge(self,ids,kind,text):
		if kind not in{'note','reply'}:raise ValueError('Every acknowledgement is a note or a reply, with its text')
		text=note_text(text);stamp=now()
		with self.transaction()as db:
			for record_id in ids:
				identifier(record_id)
				for table in('notes','submissions'):
					row=db.execute(f"SELECT acknowledged_at, replies FROM {table} WHERE id = ?",(record_id,)).fetchone()
					if row is None:continue
					if row['acknowledged_at']is None:db.execute(f"UPDATE {table} SET acknowledged_at = ?, ack_kind = ?, ack_text = ?, seen_at = COALESCE(seen_at, ?) WHERE id = ?",(stamp,kind,text,stamp,record_id))
					else:more=replies_list(row['replies'])+[{'kind':kind,'text':text,'at':stamp}];db.execute(f"UPDATE {table} SET replies = ?, ack_edited_at = ? WHERE id = ?",(json.dumps(more,ensure_ascii=False),stamp,record_id))
					if table=='submissions':db.execute('UPDATE reports SET agent_seen_at = ? WHERE id = (SELECT report_id FROM submissions WHERE id = ?)',(stamp,record_id))
					break
				else:raise ValueError(f"Unknown note: {record_id}; no receipts written")
			db.execute("INSERT OR REPLACE INTO meta VALUES ('last_check', ?)",(stamp,))
			if not gate_pending_count(db):db.execute("INSERT OR REPLACE INTO meta VALUES (?, '0')",(POLLS_SINCE_MESSAGE,))
	def guard_report_change(self,db,report_id,report,hold_view=True):
		unread=db.execute('SELECT 1 FROM submissions WHERE report_id = ? AND acknowledged_at IS NULL LIMIT 1',(report_id,)).fetchone()
		if unread is not None:raise ReportHeld('The report has an unacknowledged answer.')
		acknowledged=db.execute('SELECT MAX(acknowledged_at) FROM submissions WHERE report_id = ?',(report_id,)).fetchone()[0]
		if acknowledged and not at_or_after(report['ack_seen_at'],acknowledged):raise ReportHeld('The report waits for the owner: its answer acknowledgement is unseen.')
		if not hold_view:return
		answered=db.execute('SELECT 1 FROM submissions WHERE report_id = ? LIMIT 1',(report_id,)).fetchone();age=seconds_since(report['viewed_at'])
		if answered is not None and age is not None and 0<=age<REPORT_CHANGE_VIEW_SECONDS:remaining=max(1,int(REPORT_CHANGE_VIEW_SECONDS-age));raise ReportHeld(f"The owner is reading this report: retry after {remaining} s without a view.")
	def publish(self,report_id,title,source):
		identifier(report_id)
		if not isinstance(title,str)or not title.strip()or len(title)>200:raise ValueError('Report title must contain 1–200 characters')
		source=Path(source)
		if source.suffix.lower()!='.md':raise ValueError('Publish a UTF-8 .md source file')
		with source.open('rb')as stream:data=stream.read(MAX_REPORT+1)
		if len(data)>MAX_REPORT:raise ValueError('Report exceeds the 2 MB limit; split it into reports')
		text=data.decode('utf-8');fields=parse_fields(text)[1]
		with self.transaction()as db:
			report=db.execute('SELECT ack_seen_at, viewed_at FROM reports WHERE id = ?',(report_id,)).fetchone()
			if report is not None:self.guard_report_change(db,report_id,report)
			else:
				answered=db.execute('SELECT 1 FROM submissions WHERE report_id = ? LIMIT 1',(report_id,)).fetchone()
				if answered is not None:raise ValueError(f"Report {report_id} has submitted answers; publish the update under a new ID")
			self.refuse_shared_id(db,'reports','tasks',report_id);highest=db.execute('SELECT COALESCE(MAX(seq), 0) FROM reports').fetchone()[0];db.execute('INSERT INTO reports (id, title, markdown, updated_at, published_at, seq)\n           VALUES (?, ?, ?, ?, ?, ?)\n           ON CONFLICT(id) DO UPDATE SET title = excluded.title,\n             markdown = excluded.markdown, updated_at = excluded.updated_at,\n             seq = COALESCE(reports.seq, excluded.seq), seen_at = NULL',(report_id,title,text,now(),now(),highest+1))
		return len(fields)
	def unpublish(self,report_id,dismissed_by_owner=False):
		identifier(report_id)
		with self.transaction()as db:
			row=db.execute('SELECT title, ack_seen_at, viewed_at FROM reports WHERE id = ?',(report_id,)).fetchone()
			if row is None:raise FileNotFoundError('Report not found')
			if not dismissed_by_owner:self.guard_report_change(db,report_id,row,hold_view=False)
			db.execute('DELETE FROM reports WHERE id = ?',(report_id,));db.execute('INSERT OR REPLACE INTO meta VALUES (?, ?)',(REMOVED_META,now()))
			if dismissed_by_owner:db.execute('INSERT INTO notes (id, text, at, quiet) VALUES (?, ?, ?, 1)',(new_id(),f"The owner dismissed the report {report_id} ({row['title']}).",now()))
	def clear_state(self):
		counts={}
		with self.transaction()as db:
			for table in('notes','reports','submissions','tasks','uploads','fetch_jobs'):counts[table]=db.execute(f"SELECT count(*) FROM {table}").fetchone()[0];db.execute(f"DELETE FROM {table}")
			key=db.execute('SELECT value FROM meta WHERE key = ?',(AGENT_KEY_META,)).fetchone();db.execute('DELETE FROM meta')
			if key is not None:db.execute('INSERT INTO meta VALUES (?, ?)',(AGENT_KEY_META,key[0]))
		return counts
	def mark_report_seen(self,report_id):
		identifier(report_id)
		with self.transaction()as db:
			row=db.execute('SELECT * FROM reports WHERE id = ?',(report_id,)).fetchone()
			if row is None:raise FileNotFoundError('Report not found')
			db.execute('UPDATE reports SET seen_at = COALESCE(seen_at, ?), ever_seen = 1 WHERE id = ?',(now(),report_id));return dict(db.execute('SELECT * FROM reports WHERE id = ?',(report_id,)).fetchone())
	def mark_report_ack_seen(self,report_id):
		identifier(report_id)
		with self.transaction()as db:
			row=db.execute('SELECT * FROM reports WHERE id = ?',(report_id,)).fetchone()
			if row is None:raise FileNotFoundError('Report not found')
			db.execute('UPDATE reports SET ack_seen_at = ? WHERE id = ?',(now(),report_id));return dict(db.execute('SELECT * FROM reports WHERE id = ?',(report_id,)).fetchone())
	def mark_report_viewed(self,report_id,active=True):
		identifier(report_id)
		with closing(self.connect())as db,db:
			row=db.execute('SELECT 1 FROM reports WHERE id = ?',(report_id,)).fetchone()
			if row is None:raise FileNotFoundError('Report not found')
			stamp=now()if active else None;db.execute('UPDATE reports SET viewed_at = ? WHERE id = ?',(stamp,report_id))
		return{'id':report_id,'viewed_at':clip_stamp(stamp)}
	def report(self,report_id,shared=None):
		identifier(report_id)
		with self.transaction(shared)as db:
			row=db.execute('SELECT * FROM reports WHERE id = ?',(report_id,)).fetchone()
			if row is None:raise FileNotFoundError('Report not found')
			return dict(row)
	@staticmethod
	def validate_fields(fields):
		if not 1<=len(fields)<=50:raise ValueError('A report holds 1–50 fields')
		seen=set()
		for field in fields:
			field_id=field['id'];identifier(field_id)
			if field_id in seen:raise ValueError(f"Duplicate field ID: {field_id}")
			seen.add(field_id);prompt=field['prompt']
			if not prompt.strip()or len(prompt)>500:raise ValueError('Each prompt is 1–500 characters')
			if field['type']=='text':continue
			options=field['options']
			if not 1<=len(options)<=20 or len(set(options))!=len(options)or any(not option.strip()or len(option)>200 for option in options):raise ValueError(f"{field['type']} fields take 1–20 unique options of 1–200 characters")
			labels=[custom_label(option)for option in options];labels=[label for label in labels if label is not None]
			if len(set(labels))!=len(labels):raise ValueError(f"{field['type']} fields give each free-text option its own label")
	def submit_report(self,report_id,note_id,answers,revision=None):
		if not isinstance(revision,str)or not revision:raise ValueError('Report revision required; copy your entries and reload the preview')
		with self.transaction()as db:
			db.execute('BEGIN IMMEDIATE');report=self.report(report_id,shared=db)
			if revision!=report['updated_at']:raise ReportChanged('Report changed. Your entries are kept; copy them before refreshing, reviewing and resending')
			fields=parse_fields(report['markdown'])[1]
			if not fields:raise ValueError('This report has no fields to answer')
			return self.submit(report_id,report['title'],fields,note_id,answers,shared=db)
	def submit(self,report_id,title,fields,note_id,answers,shared=None):
		if not isinstance(answers,dict):raise TypeError('Answers is a JSON object keyed by field ID')
		known={field['id']for field in fields};unknown=set(answers)-known
		if unknown:raise ValueError(f"Unknown field IDs: {', '.join(sorted(unknown))}")
		lines=[f"REPORT {report_id} {title}:"]
		for field in fields:
			field_id=field['id'];value=answers.get(field_id)
			if field['type']=='text':
				if value is None:rendered='(skipped)'
				elif not isinstance(value,str):raise ValueError(f"{field_id}: text answers must be strings")
				else:rendered=value if value.strip()else'(skipped)'
			elif value is None:rendered='(skipped)'
			elif field['type']=='choice':
				if value not in field['options']and not custom_answer(field,value):raise ValueError(f"{field_id}: choose one of "+', '.join(field['options']))
				rendered=value
			else:
				if not isinstance(value,list)or len({item for item in value if isinstance(item,str)})!=len(value)or any(item not in field['options']and not custom_answer(field,item)for item in value):raise ValueError(f"{field_id}: pick options only: "+', '.join(field['options']))
				rendered=', '.join(value)if value else'(skipped)'
			lines.append(f"  {field_id}: {rendered}")
		return self.submission(note_id,report_id,'\n'.join(lines),shared=shared)
def open_link(renderer,tokens,index,options,env):
	token=tokens[index];href=token.attrGet('href')or''
	if href and not href.startswith('#'):token.attrSet('target','_blank');token.attrSet('rel','noopener noreferrer')
	return renderer.renderToken(tokens,index,options,env)
def require_renderer():
	if not HAS_RENDERER:raise SystemExit("serve needs markdown-it-py: install it in the preview venv with `python -m pip install markdown-it-py`, then start the server with that venv's Python. read, ack and publish work without it.")
CODE_BLOCK=re.compile('<pre>(.*?)</pre>',re.DOTALL)
CODE_TAG=re.compile('<[^>]+>')
ICON_COPY='<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><rect width="14" height="14" x="8" y="8" rx="2" ry="2"></rect><path d="M4 16c-1.1 0-2-.9-2-2V4c0-1.1.9-2 2-2h10c1.1 0 2 .9 2 2"></path></svg>'
def add_copy_buttons(rendered):
	def replace(match):inner=match.group(1);code=html.unescape(CODE_TAG.sub('',inner));payload=html.escape(code,quote=True).replace('\n','&#10;');button=f'<button type="button" class="copy-code" aria-label="Copy code" title="Copy code" data-code="{payload}">{ICON_COPY}</button>';return f'<div class="code-block">{button}<pre>{inner}</pre></div>'
	return CODE_BLOCK.sub(replace,rendered)
ESCAPED_FENCE=re.compile('^(?P<indent>[ \\t]*)\\\\(?P<fence>(?:`{3,}|~{3,}))',re.MULTILINE)
def unescape_fences(source):return ESCAPED_FENCE.sub(lambda match:match.group('indent')+match.group('fence'),source)
PARAGRAPH=re.compile('<p>.*?</p>',re.DOTALL)
PARAGRAPH_BREAK=re.compile('<br\\s*/?>')
def drop_paragraph_breaks(rendered):return PARAGRAPH.sub(lambda match:PARAGRAPH_BREAK.sub('',match.group(0)),rendered)
def sized_image(match,validate):
	alt,source,width,height=match.groups()
	if not validate(source):return None
	size=f' width="{width}"'+(f' height="{height}"'if height else'');return f'<img src="{html.escape(source,quote=True)}" alt="{html.escape(alt,quote=True)}"{size} />'
def render(markdown,breaks=False):
	try:from markdown_it import MarkdownIt;from markdown_it.common.normalize_url import validateLink
	except ImportError as error:raise RuntimeError("Markdown rendering needs markdown-it-py. Install it in the preview's venv and restart the server with that venv's Python; steering still works.")from error
	parser=MarkdownIt('commonmark',{'html':False,'breaks':breaks}).enable(['table','strikethrough']);parser.add_render_rule('link_open',open_link);tagged,images=hold_out(markdown,SIZED_IMAGE,lambda match:sized_image(match,validateLink),'image');rendered=drop_paragraph_breaks(parser.render(unescape_fences(tagged)));return add_copy_buttons(put_back(rendered,images,'image'))
def handler(store):
	token=secrets.token_urlsafe(32)
	class Handler(BaseHTTPRequestHandler):
		def setup(self):super().setup();self.connection.settimeout(15)
		def reply(self,status,body,content_type='application/json; charset=utf-8',filename=None):
			data=body if isinstance(body,(bytes,bytearray))else body.encode('utf-8');self.send_response(status);self.send_header('Content-Type',content_type);self.send_header('Content-Length',str(len(data)));self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff');self.send_header('Content-Security-Policy',"default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src 'self' data: https:; connect-src 'self' https:; base-uri 'none'; form-action 'self'")
			if filename:safe=header_filename(filename);self.send_header('Content-Disposition',f'attachment; filename="{safe}"')
			self.end_headers();self.wfile.write(data)
		def problem(self,status,error):self.reply(status,json.dumps({'error':str(error)}))
		def do_GET(self):
			path=urlsplit(self.path).path
			try:
				if path=='/':page=(ASSETS/'index.html').read_text(encoding='utf-8');page=page.replace('__STYLE__',(ASSETS/'style.css').read_text(encoding='utf-8'));page=page.replace('__SCRIPT__',(ASSETS/'app.js').read_text(encoding='utf-8'));self.reply(200,page.replace('__TOKEN__',token),'text/html; charset=utf-8');return
				if path=='/api/state':
					state=store.state();state['token']=token
					try:
						for item in state['notes']+[ack for report in state['reports']for ack in report['acknowledgements']]:
							if'text'in item:item['html']=render(item['text'])
							if item.get('ack_kind')=='reply'and item.get('ack_text'):item['ack_html']=render(item['ack_text'].replace('\\n','\n'))
							for reply in item.get('replies')or[]:
								if reply['kind']=='reply':reply['html']=render(reply['text'].replace('\\n','\n'))
					except RuntimeError as error:state['rendering_error']=str(error)
					self.reply(200,json.dumps(state,ensure_ascii=False));return
				if path=='/api/submissions':live={report['id']for report in store.state()['reports']};self.reply(200,json.dumps([saved_answer_line(record)for record in store.submissions()if record['report_id']in live],ensure_ascii=False),'application/json; charset=utf-8');return
				if path=='/api/copy-state':notes=store.state()['notes'];lines,counts=store.state_lines(notes,store.tasks()or{});stamps=store.newest_stamps();self.reply(200,json.dumps({'text':state_ndjson(lines),'counts':counts,'stamp':store.newest_stamp(stamps),'stamps':{'note':stamps['note'],'task':stamps['task']},'repo':store.path.parent.parent.name,'branch':workspace_branch(store.path.parent.parent)},ensure_ascii=False),'application/json; charset=utf-8');return
				if path=='/api/report-sources':self.reply(200,json.dumps([saved_report_line(record)for record in store.report_sources()],ensure_ascii=False),'application/json; charset=utf-8');return
				match=re.fullmatch('/api/reports/([a-zA-Z0-9_-]{1,80})/(html|source)',path)
				if match:
					report_id,kind=match.groups();report=store.report(report_id)
					if kind=='html':
						try:body,questions=render_report(report['markdown'])
						except ValueError as error:self.problem(500,f"This report cannot be rendered: {error}");return
						self.reply(200,json.dumps({'html':body,'fields':len(questions),'revision':report['updated_at'],'published':report['published_at']or report['updated_at'],'edited':report['updated_at']},ensure_ascii=False));return
					self.reply(200,report['markdown'],'text/plain; charset=utf-8',f"{report_id}.md");return
				self.problem(404,'Not found')
			except FileNotFoundError as error:self.problem(404,error)
			except(OSError,sqlite3.Error,RuntimeError)as error:self.problem(503,error)
		def do_POST(self):
			path=urlsplit(self.path).path;report_submit=re.fullmatch('/api/reports/([a-zA-Z0-9_-]{1,80})/submit',path);report_seen=re.fullmatch('/api/reports/([a-zA-Z0-9_-]{1,80})/seen',path);report_ack_seen=re.fullmatch('/api/reports/([a-zA-Z0-9_-]{1,80})/ack-seen',path);report_view=re.fullmatch('/api/reports/([a-zA-Z0-9_-]{1,80})/view',path);message_replies_seen=re.fullmatch('/api/messages/([a-zA-Z0-9_-]{1,80})/replies/seen',path);report_unpublish=re.fullmatch('/api/reports/([a-zA-Z0-9_-]{1,80})/unpublish',path);fetch_post=re.fullmatch('/api/fetch-jobs/([a-zA-Z0-9_-]{1,80})/(renew|result|fail|retry|approve|deny|drop)',path);note_upload=path=='/api/notes/with-file';agent_key_post=path=='/api/key';skip_poll_post=path=='/api/skip-poll';fetch_result=bool(fetch_post and fetch_post.group(2)=='result')
			if path not in{'/api/notes','/api/markdown','/api/fetch-jobs','/api/fetch-jobs/claim'}and not report_submit and not report_seen and not report_ack_seen and not report_view and not message_replies_seen and not report_unpublish and not note_upload and not fetch_post and not agent_key_post and not skip_poll_post:self.problem(404,'Not found');return
			content_type=self.headers.get('Content-Type','')
			if note_upload:
				if not content_type.lower().startswith('multipart/form-data;'):self.problem(415,'Expected multipart/form-data');return
			elif content_type!='application/json'and not fetch_result:self.problem(415,'Expected application/json');return
			try:
				if self.headers.get('Transfer-Encoding')or len(self.headers.get_all('Content-Length',[]))!=1:raise ValueError('Send one Content-Length and no Transfer-Encoding')
				length=int(self.headers.get('Content-Length','0'));limit=None
				if fetch_result:
					with closing(store.connect())as db:job=store.claimed_fetch(db,fetch_post.group(1),self.headers.get('X-Fetch-Claim',''));limit=MAX_FETCH if job['origin']!='owner'else None
				if length<=0 or limit is not None and length>limit:
					subject='Upload'if note_upload else'Download'if fetch_result else'Request body';remaining=length if limit is not None and 0<length<=limit+1 else 0
					while remaining>0:
						chunk=self.rfile.read(min(65536,remaining))
						if not chunk:break
						remaining-=len(chunk)
					self.problem(413,f"{subject} has an invalid length"if limit is None else f"{subject} must be 1–{limit:,} bytes");return
				if note_upload:
					with stream_note_attachments(content_type,self.rfile,length)as parsed:note=store.note_with_uploads(*parsed)
					for key in('at','acknowledged_at','ack_edited_at','seen_at'):note[key]=clip_stamp(note[key])
					self.reply(201,json.dumps(note,ensure_ascii=False));return
				if fetch_result:
					with tempfile.TemporaryFile()as stream:
						data=FileBody(stream);remaining=length
						while remaining:
							chunk=self.rfile.read(min(65536,remaining))
							if not chunk:raise ValueError('Incomplete request body')
							data.write(chunk);remaining-=len(chunk)
						name=parse_qs(urlsplit(self.path).query).get('name',[''])[0];record=store.complete_fetch(fetch_post.group(1),self.headers.get('X-Fetch-Claim',''),name,self.headers.get('Content-Type',''),self.headers.get('X-Fetch-Source',''),data)
					for key in('at','updated_at'):record[key]=clip_stamp(record[key])
					self.reply(201,json.dumps(record,ensure_ascii=False));return
				data=self.rfile.read(length)
				if len(data)!=length:self.problem(400,'Incomplete request body; retry the upload or request');return
				payload=json.loads(data)
				if not isinstance(payload,dict):self.problem(400,'Expected a JSON object');return
				if agent_key_post:
					candidate=payload.get('key')
					if not isinstance(candidate,str)or not AGENT_KEY_RE.fullmatch(candidate):raise ValueError('key must be 20 to 64 letters, digits, dashes or underscores')
					host=payload.get('host')
					if host is not None and(not isinstance(host,str)or not AGENT_HOST_RE.fullmatch(host)):raise ValueError('host must be one https origin, with no path')
					store.set_agent_key(candidate,host);self.reply(200,json.dumps(store.agent_key(),ensure_ascii=False));return
				if skip_poll_post:
					armed=payload.get('skip',True)
					if not isinstance(armed,bool):raise ValueError('skip must be true or false')
					self.reply(200,json.dumps(store.request_skip_poll(armed),ensure_ascii=False));return
				if path=='/api/fetch-jobs':record=store.enqueue_fetch(payload.get('url'),payload.get('allow_proxy',False));self.reply(201,json.dumps(record,ensure_ascii=False));return
				if path=='/api/fetch-jobs/claim':self.reply(200,json.dumps({'job':store.claim_fetch()},ensure_ascii=False));return
				if fetch_post:
					job_id,action=fetch_post.groups()
					if action=='renew':record=store.renew_fetch(job_id,self.headers.get('X-Fetch-Claim',''))
					elif action=='fail':record=store.fail_fetch(job_id,self.headers.get('X-Fetch-Claim',''),payload.get('error',''))
					elif action in{'approve','deny'}:record=store.decide_fetch(job_id,'approved'if action=='approve'else'denied')
					elif action=='drop':record=store.drop_fetch(job_id)
					else:record=store.retry_fetch(job_id)
					self.reply(200,json.dumps(record,ensure_ascii=False));return
				if path=='/api/markdown':self.reply(200,render(owner_text(payload.get('text')),breaks=True),'text/html; charset=utf-8');return
				if report_seen:
					report=store.mark_report_seen(report_seen.group(1))
					for key in('updated_at','seen_at'):report[key]=clip_stamp(report[key])
					self.reply(200,json.dumps(report,ensure_ascii=False));return
				if report_view:self.reply(200,json.dumps(store.mark_report_viewed(report_view.group(1),payload.get('active',True)),ensure_ascii=False));return
				if report_ack_seen:
					report=store.mark_report_ack_seen(report_ack_seen.group(1))
					for key in('updated_at','seen_at','ack_seen_at'):report[key]=clip_stamp(report[key])
					self.reply(200,json.dumps(report,ensure_ascii=False));return
				if message_replies_seen:seen=store.mark_replies_seen(message_replies_seen.group(1),payload.get('count'));self.reply(200,json.dumps(seen,ensure_ascii=False));return
				if report_unpublish:store.unpublish(report_unpublish.group(1),dismissed_by_owner=True);self.reply(200,json.dumps({'unpublished':report_unpublish.group(1)}));return
				if report_submit:
					note=store.submit_report(report_submit.group(1),payload.get('id'),payload.get('answers'),payload.get('revision'))
					for key in('at','acknowledged_at','ack_edited_at','seen_at'):note[key]=clip_stamp(note[key])
					self.reply(201,json.dumps(note,ensure_ascii=False));return
				note=store.note(payload.get('id'),payload.get('text'),quiet=bool(payload.get('quiet')))
				for key in('at','acknowledged_at','ack_edited_at','seen_at'):note[key]=clip_stamp(note[key])
				self.reply(201,json.dumps(note,ensure_ascii=False))
			except(ReportChanged,FetchChanged,ReportHeld)as error:self.problem(409,error)
			except FileNotFoundError as error:self.problem(404,error)
			except(ValueError,TypeError,UnicodeDecodeError)as error:self.problem(400,error)
			except(OSError,sqlite3.Error,RuntimeError)as error:self.problem(503,error)
	return Handler
def resolve_repo_root():
	for parent in Path(__file__).resolve().parents:
		if(parent/'.git').exists():return str(parent)
	return str(Path.cwd())
def resolve_state_dir():
	configured=os.environ.get('ARENA_PREVIEW_STATE_DIR')
	if configured:return configured
	for parent in Path(__file__).resolve().parents:
		if(parent/'.git').exists():return str(parent/'arena-state')
	return'arena-state'
def ensure_installed():
	if os.environ.get('ARENA_PREVIEW_NO_BOOTSTRAP')or shutil.which('arena-preview'):return
	for ancestor in Path(__file__).resolve().parents:
		installer=ancestor/'.agents'/'skills'/'arena-skill'/'scripts'/'install.sh'
		if installer.is_file():
			try:subprocess.run(['bash',str(installer)],cwd=str(ancestor),check=False)
			except OSError:pass
			return
def main():
	parser=argparse.ArgumentParser(description=CLI_DESCRIPTION);parser.add_argument('-r','--reminder',action='store_true',help='Print the unacked-count reminder line and exit');commands=parser.add_subparsers(dest='command',required=False);serve=commands.add_parser('serve',help='Serve the preview page; -p names the port');serve.add_argument('-p','--port',type=int,default=DEFAULT_PORT,help=f"Port to bind (default: {DEFAULT_PORT})");commands.add_parser('init',help='Create the state directory and its database');commands.add_parser('read',help='Print every pending note, answer and upload');note_list=commands.add_parser('note-list',help='List stored note IDs by page, newest first, including acknowledged notes');note_list.add_argument('--page',type=int,default=1,help='Page number (default: 1)');note_list.add_argument('--page-size',type=int,default=20,help='IDs per page, 1 to 100 (default: 20)');note_reread=commands.add_parser('note',help='Reread a stored note by its short or full ID');note_reread.add_argument('id',help='Six or all characters of a note ID');commands.add_parser('key',help='Print the recorded agent key, host and use line');inbox_line=commands.add_parser('inbox-line',help='Report whether a command line that names an inbox call runs nothing else');inbox_line.add_argument('line',help='The command line, as the hook read it');route=commands.add_parser('route',help='Start a command detached, keep its output tail, and show the exit on a later call');route.add_argument('name',help='A short name for the run, for example gates');route.add_argument('command_args',nargs=argparse.REMAINDER,help='The command, after --');route_run=commands.add_parser('route-run',help='Run one command here and record its exit for a later call');route_run.add_argument('name',help='The run name the caller already recorded');route_run.add_argument('command_args',nargs=argparse.REMAINDER,help='The command, after --');logs=commands.add_parser('logs',help="Print a routed run's status and its recorded output tail");logs.add_argument('name',help='The run name to read');worktree=commands.add_parser('worktree',help='Create or collect a detached task worktree beside a root gate');worktree_action=worktree.add_subparsers(dest='worktree_action',required=True);worktree_start=worktree_action.add_parser('start',help='Create a detached worktree while a named root gate runs');worktree_start.add_argument('name',help='A short worktree name');worktree_start.add_argument('destination',type=Path,help='A new path outside the root');worktree_start.add_argument('--gate',required=True,help='The running root gate name');worktree_start.add_argument('--allow',action='append',default=[],help='A path glob task work may change');worktree_collect=worktree_action.add_parser('collect',help='Print the allowed detached-worktree patch after a green root gate');worktree_collect.add_argument('name',help='The worktree name to collect');worktree_tick=commands.add_parser('worktree-tick',help='Block a gate, commit or push from a detached task worktree');worktree_tick.add_argument('action',choices=sorted(WORKTREE_ACTIONS));ack_tick=commands.add_parser('ack-tick',help='Warn when an inline ack text holds a backtick the shell will substitute');ack_tick.add_argument('line',help='The command line, as the hook read it');serve_tick=commands.add_parser('serve-tick',help='Warn when a serve names a port other than the default');serve_tick.add_argument('line',help='The command line, as the hook read it');pr_check_tick=commands.add_parser('pr-check-tick',help='Refuse a bash line that runs a PR-check wait the background tool must take');pr_check_tick.add_argument('line',help='The command line, as the hook read it');pr_check_tick.add_argument('--command',dest='current_command',help='The command the DEBUG trap reached');egress_tick=commands.add_parser('egress-tick',help='Warn when a line fetches a host the sandbox egress filter blocks');egress_tick.add_argument('line',help='The command line, as the hook read it');rename=commands.add_parser('rename',help='Move a note, answer, report or task to a new ID');rename.add_argument('old_id',help='The stored ID, or a prefix that names one record');rename.add_argument('new_id',help='The ID to move the record to');commit_tick=commands.add_parser('commit-tick',help='Block a commit whose staged lines cite a session-local ID');commit_tick.add_argument('--repo',default='.',help='The repository to read the staged diff from');gate=commands.add_parser('gate',help='Read the inbox gate; -p blocks on any unacked note, -l names the blocked line');gate.add_argument('-p','--push',action='store_true',help='Block while any note or answer awaits an ack, whatever the call count');gate.add_argument('-l','--line',default='',help='The command line, as the hook read it; a blocked call then names the commands to drop');commands.add_parser('poll',help='Block until the owner answers or a gate run finishes');download=commands.add_parser('download-request',help='Request an HTTPS browser download, pending a preview Approve click; -a allows the proxy fallback');download.add_argument('url',help='One HTTPS URL without embedded credentials');download.add_argument('-a','--allow-proxy',action='store_true',help='Let the owner opt in to AllOrigins and CodeTabs fallback for this request');ack=commands.add_parser('ack',help='Answer a note; a bare call answers the one pending item; -r replies, -n notes, -R and -N read the answer from a file');ack.add_argument('ids',nargs='*');ack.add_argument('-r','--reply',help='Markdown answer shown in the message log');ack.add_argument('-n','--note',help='Short plain answer shown in the message log');ack.add_argument('-R','--reply-file',type=Path,help='Read the Markdown answer from this file');ack.add_argument('-N','--note-file',type=Path,help='Read the plain answer from this file');publish=commands.add_parser('publish',help='Publish a report; -i names the ID, -t the title');publish.add_argument('source',type=Path);publish.add_argument('-i','--id',required=True);publish.add_argument('-t','--title',required=True);unpublish=commands.add_parser('unpublish',help='Withdraw a published report by its ID');unpublish.add_argument('report_id');commands.add_parser('stale-reports',help='List stale published report IDs and reasons without changing state');task=commands.add_parser('task',help='Write or update a task; TASK-ID alone prints the stored row; -s status, -o order, -d details, -m note, -r report, -b blocked, -u unblocked, -a amend');task.add_argument('id_arg',nargs='?',metavar='TASK-ID');task.add_argument('title_arg',nargs='?',metavar='TASK-TITLE');task.add_argument('detail_arg',nargs='*',metavar='TASK-DETAIL');task.add_argument('-d','--details','--task-details',dest='task_details',action='append',help='One step per line, 120 characters or fewer, repeatable; an empty string clears the list');task.add_argument('-b','--blocked',dest='blocked',action='store_true',default=None,help='Mark the task blocked, so a poll may wait');task.add_argument('-u','--unblocked',dest='blocked',action='store_false',help='Clear the blocked mark');task.add_argument('-m','--msg-id',help='Message this task answers; marks that message as having a task');task.add_argument('-r','--report',metavar='REPORT-ID',help='Report this task waits on; its answer clears the blocked mark');task.add_argument('-a','--amend',metavar='PREV-ID',help='Rename the task stored under this ID to the one given');task.add_argument('-s','--status',choices=TASK_STATUSES,default=None);task.add_argument('-o','--order',type=int,default=None,help='1-based place in its div, not the end');task_remove=commands.add_parser('task-remove',help='Delete a task by its ID');task_remove.add_argument('task_id');task_list=commands.add_parser('task-list',help='List the tasks; -f includes the finished ones');task_list.add_argument('-f','--full',action='store_true',help='print every task, finished included');state_import=commands.add_parser('import-state',help='Import an NDJSON backup; replaces the live state unless --merge; -f forces');state_import.add_argument('source',nargs='?',type=Path);state_import.add_argument('-r','--replace-tasks',action='store_true');state_import.add_argument('--merge',action='store_true',help='keep the live notes, answers, reports and tasks, and merge the import by ID');commands.add_parser('clear-state',help='Empty every state table in place; the agent key record survives');args=parser.parse_args();state_dir=resolve_state_dir();fresh_state=not(Path(state_dir).resolve()/'state.sqlite3').is_file()
	try:
		if args.reminder:
			store=Store(state_dir,create=False);require_server(store);reminder=store.reminder(advance=True);store.set_meta(AGENT_CALL_ENDED_META,now())
			if'unacked 'in reminder or not run_notices(state_dir,show_running=True):print(reminder,flush=True)
			return 0
		if not args.command:parser.error('a command is required')
		if args.command=='inbox-line':return 0 if quiet_inbox_line(args.line)else 1
		if args.command=='worktree-tick':
			warning=worktree_warning(state_dir,args.action)
			if warning:print(warning,file=sys.stderr);return 1
			return 0
		if args.command=='worktree':
			if args.worktree_action=='start':record=start_worktree(state_dir,args.name,args.destination,args.gate,args.allow);print(f"Worktree {record['name']} at {record['worktree']}");return 0
			print(collect_worktree(state_dir,args.name),end='');return 0
		if args.command=='route':
			warning=worktree_warning(state_dir,'gate')
			if warning:print(warning,file=sys.stderr);return 1
			return route_command(state_dir,args.name,args.command_args)
		if args.command=='route-run':
			warning=worktree_warning(state_dir,'gate')
			if warning:print(warning,file=sys.stderr);return 1
			return run_command(state_dir,args.name,args.command_args)
		if args.command=='logs':return run_log_report(state_dir,args.name)
		if args.command=='ack-tick':
			warning=tick_warning(args.line)
			if warning:print(warning,file=sys.stderr)
			return 0
		if args.command=='serve-tick':
			warning=serve_warning(args.line)
			if warning:print(warning,file=sys.stderr)
			return 0
		if args.command=='egress-tick':
			warning=egress_warning(args.line)
			if warning:print(warning,file=sys.stderr)
			return 0
		if args.command=='pr-check-tick':return pr_check_wait(state_dir,args.line,args.current_command)
		if args.command=='rename':
			try:old_id=Store(state_dir).resolve_id(args.old_id)
			except FileNotFoundError:print(f"No note, answer, report or task holds the ID {args.old_id}");return 1
			except ValueError as error:print(error);return 1
			try:kind=Store(state_dir).rename_entity(old_id,args.new_id)
			except ValueError as error:print(error);return 1
			print(f"ok {kind} {old_id} -> {args.new_id}");return 0
		if args.command=='commit-tick':
			warning=worktree_warning(state_dir,'commit',cwd=args.repo)
			if warning:print(warning,file=sys.stderr);return 1
			try:store=Store(state_dir)
			except FileNotFoundError:return 0
			report=commit_id_report(staged_session_id_hits(store,args.repo))
			if report:print(report,file=sys.stderr);return 1
			return 0
		if args.command=='gate':
			run_notices(state_dir)
			if poll_timeout_line(args.line):print('TIMEOUT BANNED: never wrap `arena-preview poll` in shell `timeout`. The poll holds its own 1800-second span. Set the bash tool timeout 1800 instead, so a cut wait still returns its listing.',flush=True);return 1
			try:gate_store=Store(state_dir);allowed=gate_store.gate(pending_only=args.push);blocked_calls=gate_store.calls_since_message()if not allowed and not args.push else None
			except FileNotFoundError:allowed=True;blocked_calls=None
			except Exception:return 2
			if not allowed:
				if blocked_calls is not None:print(f"BASH BLOCKED AFTER {blocked_calls} CALLS.",flush=True)
				if args.push:print('PUSH BLOCKED: a note or answer awaits an ack, so nothing left the sandbox. Read the inbox, ack every item, push again.',flush=True)
				print('READ INBOX NOW. Only a bare `arena-preview read` passes. Then ack every note: `arena-preview ack <id> --reply <markdown>` or `arena-preview ack <id> --note <text>`.',flush=True);hint=gate_line_hint(args.line)
				if hint:print(hint,flush=True)
				blocker=first_blocking_piece(args.line)
				if blocker:blocker=shell_command_text(blocker);print(f"Blocked by the part `{blocker}`. Run the inbox calls alone, or drop this part.",flush=True)
				return 1
			if args.push and main_identical():print('HEAD equals `origin/main`, so the push carries nothing. Start new work from `origin/main`.',flush=True);return 1
			return 0
		store=Store(state_dir,create=args.command in{'serve','init','import-state'});read_only=args.command in{'stale-reports','note-list'}
		if not read_only:print(store.reminder(),file=sys.stderr,flush=True)
		if args.command!='serve'and not read_only:store.touch_agent()
		if args.command=='serve':
			require_renderer()
			with ThreadingHTTPServer(('0.0.0.0',args.port),handler(store))as server:store.set_meta('port',str(server.server_port));print(f"Preview on 0.0.0.0:{server.server_port}; state: {store.path}",flush=True);server.serve_forever()
		elif args.command=='read':require_server(store);store.notice_expired_key();print_read(store)
		elif args.command=='note-list':
			ids,total=store.list_note_ids(args.page,args.page_size);pages=max(1,(total+args.page_size-1)//args.page_size);print(f"Notes page {args.page}/{pages} ({total} total; newest first)")
			for note_id in ids:print(note_id)
		elif args.command=='note':
			rows=store.notes_matching(args.id)
			if not rows:print(f"No note matches {args.id}.",file=sys.stderr);return 1
			for row in rows:head=f"note {row['id']} at {row['at']}";head+=f" acked {row['acknowledged_at']}"if row['acknowledged_at']else' unacked';print(head);print(row['text']);print()
		elif args.command=='key':
			record=store.agent_key()
			if not record:print('No agent key recorded.',file=sys.stderr);return 1
			print(format_key(record))
		elif args.command=='poll':require_server(store);store.notice_expired_key();return poll_inbox(store)
		elif args.command=='download-request':print(format_fetch_job(store.enqueue_fetch(args.url,args.allow_proxy,pending=True)))
		elif args.command=='ack':
			sources=args.reply,args.note,args.reply_file,args.note_file
			if sum(source is not None for source in sources)!=1:raise ValueError('Choose exactly one of --reply, --note, --reply-file or --note-file')
			if args.reply is not None:kind,text='reply',args.reply
			elif args.note is not None:kind,text='note',args.note
			else:
				source=args.reply_file if args.reply_file is not None else args.note_file;kind='reply'if args.reply_file is not None else'note'
				try:text=source.read_text(encoding='utf-8')
				except OSError as error:raise ValueError(f"Cannot read {source}: {error.strerror or error}")from None
			ids=[store.resolve_id(token)for token in args.ids]if args.ids else[store.pending_ack()];store.acknowledge(ids,kind,text);short=[i[:6]for i in ids];print('ok '+' '.join(short));print('task? '+'; '.join(f'task <id> "<title>" -m {i}'for i in ids))
		elif args.command=='publish':
			count=store.publish(args.id,args.title,args.source);print(f"Published {args.id}, {count} fields.")
			if not count and'{#'in Path(args.source).read_text('utf-8'):print('Warning: 0 fields parsed. A `{#id}` marker ends a prompt line, then the `- ( ) option` lines follow.',file=sys.stderr)
		elif args.command=='unpublish':report_id=store.resolve_id(args.report_id);store.unpublish(report_id);print(f"Unpublished {report_id}. Answers and source file remain.")
		elif args.command=='stale-reports':
			reports=store.stale_reports();print(f"Stale reports {len(reports)}")
			for report in reports:print(f"{report['id']}\t{report['reason']}")
		elif args.command=='task':
			task_id=args.id_arg
			if not task_id:raise ValueError('A task needs an ID')
			if args.title_arg is None and not args.detail_arg and args.task_details is None and args.status is None and args.order is None and args.blocked is None and args.report is None and args.msg_id is None and args.amend is None:
				resolved=store.resolve_id(task_id);record=store.task_record(resolved)
				if record is None:raise ValueError(f"No task is stored under {resolved}")
				print(format_task(record));return 0
			details=args.task_details
			if details is None and args.detail_arg:details=args.detail_arg
			if details is not None:details=task_steps(details);check_task_steps(details)
			if args.amend:store.amend_task(store.resolve_id(args.amend),task_id)
			if args.report:
				try:store.report(args.report)
				except FileNotFoundError:raise ValueError(f"No report is stored under {args.report}")from None
			if args.msg_id:
				with store.transaction()as shared:record=store.write_task(task_id,args.title_arg,details,args.status,args.order,args.blocked,args.report,shared=shared);store.mark_task(args.msg_id,task_id,shared=shared)
			else:record=store.write_task(task_id,args.title_arg,details,args.status,args.order,args.blocked,args.report)
			print(format_task(record))
		elif args.command=='task-remove':print(format_task(store.remove_task(store.resolve_id(args.task_id))))
		elif args.command=='task-list':print(format_task_list(store.list_tasks(),args.full))
		elif args.command=='import-state':text=args.source.read_text(encoding='utf-8')if args.source else sys.stdin.read();print(format_import_receipt(store.import_state(text,args.replace_tasks,args.merge)))
		elif args.command=='clear-state':print(format_clear_state(store.clear_state()))
		elif args.command=='init':print(f"state {store.path} {'created'if fresh_state else'present'}")
	except(OSError,ValueError,TypeError,KeyError,sqlite3.Error,RuntimeError)as error:print(f"Preview error: {error}",file=sys.stderr);return 1
	return 0
if __name__=='__main__':ensure_installed();raise SystemExit(main())
