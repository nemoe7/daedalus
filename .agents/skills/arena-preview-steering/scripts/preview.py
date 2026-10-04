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
import time
import uuid
from contextlib import closing,contextmanager,nullcontext
from datetime import datetime,timedelta,timezone
from email import policy
from email.parser import BytesParser
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs,urlsplit,urlunsplit
try:import markdown_it;HAS_RENDERER=True
except ImportError:HAS_RENDERER=False
ASSETS=Path(__file__).resolve().parents[1]/'assets'
IDENTIFIER=re.compile('[a-zA-Z0-9_-]{1,80}\\Z')
MAX_REPORT=2000000
MAX_NOTE=15000
MAX_BODY=96000
MAX_UPLOAD=50000000
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
TASK_REMINDER='You have {remaining} tasks remaining.'
def fill_reminder(tail,remaining):
	if'{remaining}'in tail and remaining==0:return None
	count=f"{remaining} task"if remaining==1 else f"{remaining} tasks";return tail.replace('{remaining} tasks',count)
def reminder_tail(cursor,remaining):
	for step in range(len(REMINDERS)):
		tail=fill_reminder(REMINDERS[(cursor+step)%len(REMINDERS)],remaining)
		if tail:return tail
	return REMINDERS[cursor%len(REMINDERS)]
REMINDERS='Refresh context with ARENA.md, SKILL.md, and REFERENCE.md.','Run `task-list` at turn start and update it as work changes.','Take the smallest open task next.','Always push.','`ask_user` on GH_TOKEN failure.','Keep docs terse but clear.','Ask questions ASAP through fielded reports; keep other work moving.',"Don't forget to publish your reports.",'Never end a turn with unblocked tasks.','Remove stale reports with unpublish.','End the turn with `poll` to wait for more work.','Grep-verify each edit landed.',TASK_REMINDER,'Rebase on `origin/main` before pushing.','No PR checks run? Rebase onto main first.',"Check the PR's CI before ending a pushed turn.",'Read the PR checks with `gh pr checks <PR> --watch`.',"Don't use the full path. Run `arena-preview` instead."
REMINDER_CURSOR='reminder_cursor'
POLLS_SINCE_MESSAGE='polls_since_message'
GATE_THRESHOLD=20
AGENT_KEY_META='agent_key'
AGENT_KEY_RE=re.compile('[A-Za-z0-9_-]{20,64}\\Z')
AGENT_HOST_RE=re.compile('https://[A-Za-z0-9.-]+\\Z')
def now():return datetime.now(timezone.utc).isoformat()
def reset_poll_count(db):
	unacked=db.execute('SELECT (SELECT count(*) FROM notes WHERE acknowledged_at IS NULL) + (SELECT count(*) FROM submissions WHERE acknowledged_at IS NULL)').fetchone()[0]
	if unacked==1:db.execute("INSERT OR REPLACE INTO meta VALUES (?, '0')",(POLLS_SINCE_MESSAGE,))
def meta_number(db,key):row=db.execute('SELECT value FROM meta WHERE key = ?',(key,)).fetchone();value=str(row[0])if row else'';return int(value)if value.isdigit()else 0
def main_identical(root='.'):
	def git(*arguments):return subprocess.run(['git',*arguments],cwd=root,capture_output=True,text=True,check=False)
	try:
		for ref in('origin/main','HEAD'):
			if git('rev-parse','--verify','--quiet',ref).returncode:return False
		return git('diff','--quiet','origin/main','HEAD','--').returncode==0
	except OSError:return False
def new_id():hexed=uuid.uuid4().hex;return f"{hexed[:7]}-{hexed[7:]}"
def seconds_since(value):
	if not value:return None
	try:return(datetime.now(timezone.utc)-datetime.fromisoformat(value)).total_seconds()
	except ValueError:return None
def clip_stamp(value):return value[:19]if value else value
def at_or_after(value,other):
	if not value or not other:return False
	return clip_stamp(value)>=clip_stamp(other)
def identifier(value):
	if not isinstance(value,str)or not IDENTIFIER.fullmatch(value):raise ValueError('ID must contain 1–80 letters, digits, underscores or hyphens')
	return value
def owner_text(text):
	if not isinstance(text,str)or not text.strip():raise ValueError('Enter a nonblank note')
	return text
def note_text(text):
	if not isinstance(text,str)or not text.strip()or len(text)>MAX_NOTE:raise ValueError(f"Enter a note of 1–{MAX_NOTE} characters")
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
	record=dict(row)
	if'replies'in record:record['replies']=replies_list(record['replies'])
	return record
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
				options.append(item.group(2))
				if item.group(1).lower()=='x':default.append(item.group(2))
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
MAX_TASK_TITLE=200
MAX_TASK_DETAIL=2000
MAX_TASK_DETAILS=40
ECHO_DETAIL=200
TASK_COLUMNS='id, title, details, status, position, updated_at, blocked'
CLI_DESCRIPTION='Notes, reports, tasks and the preview server for Arena steering.'
SAVED_STATE='saved-state.ndjson'
NOTE_LINE_KEYS='id','text','at','acknowledged_at','ack_kind','ack_text','ack_edited_at','replies','ack_edited_seen_count','seen_at','task_id'
TASK_LINE_KEYS='id','title','details','status','order'
SUBMISSION_LINE_KEYS='id','report_id','text','at','acknowledged_at','ack_kind','ack_text','ack_edited_at','replies','ack_edited_seen_count','seen_at','task_id'
def task_row(row):return{'id':row[0],'title':row[1],'details':json.loads(row[2]),'status':row[3],'order':row[4],'updated_at':row[5],'blocked':bool(row[6])if len(row)>6 else False}
def echo_task(record,before=None,after=None):return{'id':record['id'],'title':record['title'],'status':record['status'],'order':record['order'],'prev':before,'next':after,'details':[detail[:ECHO_DETAIL]+('…'if len(detail)>ECHO_DETAIL else'')for detail in record['details']]}
def saved_note_line(record):
	if not isinstance(record,dict):raise TypeError('Every saved note is an object')
	return{key:record.get(key)for key in NOTE_LINE_KEYS}
def saved_answer_line(record):
	if not isinstance(record,dict):raise TypeError('Every saved answer is an object')
	return{key:record.get(key)for key in SUBMISSION_LINE_KEYS}
def saved_task_line(record):
	if not isinstance(record,dict):raise TypeError('Every saved task is an object')
	line={key:record.get(key)for key in TASK_LINE_KEYS};line['details']=[str(item)for item in record.get('details')or[]];return line
def upload_name(name):
	cleaned=Path(str(name or'')).name.strip()
	if not cleaned:raise ValueError('An upload needs a file name')
	if any(ord(char)<32 or ord(char)==127 for char in cleaned):raise ValueError('A file name must not contain control characters')
	return cleaned
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
				def write(chunk,body=body):
					if len(body)+len(chunk)>MAX_UPLOAD:raise ValueError(f"Each attachment must be 1–{MAX_UPLOAD:,} bytes")
					body.write(chunk)
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
def parse_note_attachments(content_type,data):
	import io
	with stream_note_attachments(content_type,io.BytesIO(data),len(data))as parsed:
		note_id,text,files=parsed;result=[]
		for(name,kind,body)in files:result.append((name,kind,body.path.read_bytes()))
		return note_id,text,result
def upload_row(row,directory):path=directory/UPLOAD_DIR/row['file'];return dict(row)|{'path':str(path),'present':path.exists()}
def add_note_attachments(note,records):
	if records:note['attachment_name']=records[0]['name'];note['attachment_path']=records[0]['path'];note['attachments']=records
	return note
def cli_json(value,pretty=False):
	if pretty:return json.dumps(value,ensure_ascii=False,indent=2)
	return json.dumps(value,ensure_ascii=False,separators=(',',':'))
def require_server(store):
	port=store.meta_value('port')
	if not port:return
	with closing(socket.socket())as probe:
		probe.settimeout(1)
		if probe.connect_ex(('127.0.0.1',int(port)))==0:return
	raise ValueError(f"preview server is down; start it before polling: arena-preview serve --port {port}")
def print_read(store,pretty=False):listing=store.read();print(cli_json(listing,pretty),flush=True);store.mark_seen([item['id']for item in listing['pending']]);store.mark_reports_agent_seen([item.get('report_id')for item in listing['pending']])
POLL_INTERVAL=1
POLL_MAX_LOOPS=900
POLLING_META='polling_at'
POLL_SINCE_META='polling_since'
POLLING_FRESH_SECONDS=5.
def poll_inbox(store,pretty=False,sleeper=None):
	if sleeper is None:sleeper=time.sleep
	listing={'checked_at':None,'pending':[]};store.start_poll()
	try:
		for index in range(POLL_MAX_LOOPS):
			listing=store.read(include_quiet=False)
			if listing['pending']:full=store.read();print(cli_json(full,pretty),flush=True);store.mark_seen([item['id']for item in full['pending']]);store.mark_reports_agent_seen([item.get('report_id')for item in full['pending']]);return 0
			open_tasks=[item for item in store.list_tasks()if item['status']=='upcoming'and not item['blocked']]
			if open_tasks:listing['tasks']=open_tasks;names=', '.join(item['id']for item in open_tasks);print(f"CONTINUE: unblocked task {names} waits. Do not end the turn.",file=sys.stderr,flush=True);print(cli_json(listing,pretty),flush=True);return 0
			if index+1<POLL_MAX_LOOPS:sleeper(POLL_INTERVAL);store.stamp_polling()
	finally:store.clear_polling()
	print(cli_json(listing,pretty),flush=True);return 1
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
def check_task(task_id,title,details):
	if not TASK_ID.match(task_id or''):raise ValueError('A task ID is 1-64 characters of lowercase letters, digits and hyphens, and starts with a letter or digit')
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
class Store:
	def __init__(self,directory,create=False,save_path=None):
		directory=Path(directory).resolve();self.path=directory/'state.sqlite3';self.save_path=Path(save_path)if save_path else self.path.parent/SAVED_STATE;existed=self.path.is_file()
		if not create and not existed:raise FileNotFoundError(f"Inbox missing: {self.path}. The sandbox may have been reset, so follow the restore routine: run scripts/install.sh from the repository root, start `arena-preview serve --port 8000` with the start_process tool, then `arena-preview read`.")
		if create and not existed:directory.mkdir(parents=True,exist_ok=True,mode=448)
		with closing(self.connect())as db,db:
			db.executescript("\n        CREATE TABLE IF NOT EXISTS notes (\n          seq INTEGER PRIMARY KEY, id TEXT UNIQUE NOT NULL,\n          text TEXT NOT NULL, at TEXT NOT NULL, acknowledged_at TEXT,\n          ack_kind TEXT, ack_text TEXT, seen_at TEXT, replies TEXT,\n          ack_edited_seen_count INTEGER NOT NULL DEFAULT 0,\n          quiet INTEGER NOT NULL DEFAULT 0\n        );\n        CREATE TABLE IF NOT EXISTS reports (\n          id TEXT PRIMARY KEY, title TEXT NOT NULL,\n          markdown TEXT NOT NULL, updated_at TEXT NOT NULL, published_at TEXT,\n          seq INTEGER, seen_at TEXT,\n          ever_seen INTEGER NOT NULL DEFAULT 0, agent_seen_at TEXT\n        );\n        CREATE TABLE IF NOT EXISTS submissions (\n          seq INTEGER PRIMARY KEY, id TEXT UNIQUE NOT NULL,\n          report_id TEXT NOT NULL, text TEXT NOT NULL, at TEXT NOT NULL,\n          acknowledged_at TEXT, ack_kind TEXT, ack_text TEXT, seen_at TEXT, replies TEXT,\n          ack_edited_seen_count INTEGER NOT NULL DEFAULT 0\n        );\n        CREATE TABLE IF NOT EXISTS uploads (\n          seq INTEGER PRIMARY KEY, id TEXT UNIQUE NOT NULL, note_id TEXT,\n          name TEXT NOT NULL, type TEXT NOT NULL, size INTEGER NOT NULL,\n          sha256 TEXT NOT NULL, file TEXT NOT NULL, at TEXT NOT NULL\n        );\n        CREATE TABLE IF NOT EXISTS fetch_jobs (\n          seq INTEGER PRIMARY KEY, id TEXT UNIQUE NOT NULL,\n          url TEXT NOT NULL, allow_proxy INTEGER NOT NULL CHECK (allow_proxy IN (0, 1)),\n          status TEXT NOT NULL CHECK (status IN ('queued', 'fetching', 'saved', 'failed')),\n          approval TEXT NOT NULL DEFAULT 'approved'\n            CHECK (approval IN ('pending', 'approved', 'denied')),\n          claim TEXT, lease_until TEXT, error TEXT, source TEXT,\n          name TEXT, type TEXT, size INTEGER, sha256 TEXT, file TEXT,\n          at TEXT NOT NULL, updated_at TEXT NOT NULL\n        );\n        CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);\n        CREATE TABLE IF NOT EXISTS tasks (\n          id TEXT PRIMARY KEY,\n          title TEXT NOT NULL,\n          details TEXT NOT NULL DEFAULT '[]',\n          status TEXT NOT NULL DEFAULT 'upcoming'\n            CHECK (status IN ('upcoming', 'finished')),\n          position INTEGER NOT NULL,\n          created_at TEXT NOT NULL,\n          updated_at TEXT NOT NULL,\n          blocked INTEGER NOT NULL DEFAULT 0\n        );\n      ");columns={row['name']for row in db.execute('PRAGMA table_info(notes)')}
			for column in('ack_kind','ack_text','ack_edited_at','seen_at','task_id','replies'):
				if column not in columns:db.execute(f"ALTER TABLE notes ADD COLUMN {column} TEXT")
			if'ack_edited_seen_count'not in columns:db.execute('ALTER TABLE notes ADD COLUMN ack_edited_seen_count INTEGER NOT NULL DEFAULT 0')
			if'quiet'not in columns:db.execute('ALTER TABLE notes ADD COLUMN quiet INTEGER NOT NULL DEFAULT 0')
			if'origin'in columns:db.execute('ALTER TABLE notes DROP COLUMN origin');columns.discard('origin')
			if'seen_at'not in columns:db.execute('UPDATE notes SET seen_at = acknowledged_at WHERE seen_at IS NULL AND acknowledged_at IS NOT NULL')
			columns={row['name']for row in db.execute('PRAGMA table_info(tasks)')}
			if'blocked'not in columns:db.execute('ALTER TABLE tasks ADD COLUMN blocked INTEGER NOT NULL DEFAULT 0')
			columns={row['name']for row in db.execute('PRAGMA table_info(submissions)')}
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
			columns={row['name']for row in db.execute('PRAGMA table_info(reports)')}
			if'published_at'not in columns:db.execute('ALTER TABLE reports ADD COLUMN published_at TEXT');db.execute('UPDATE reports SET published_at = updated_at WHERE published_at IS NULL')
			columns={row['name']for row in db.execute('PRAGMA table_info(uploads)')}
			if'note_id'not in columns:db.execute('ALTER TABLE uploads ADD COLUMN note_id TEXT');db.execute('UPDATE uploads SET note_id = id WHERE note_id IS NULL')
			db.execute('CREATE INDEX IF NOT EXISTS uploads_note_id ON uploads(note_id)');columns={row['name']for row in db.execute('PRAGMA table_info(fetch_jobs)')}
			if'approval'not in columns:db.execute("ALTER TABLE fetch_jobs ADD COLUMN approval TEXT NOT NULL DEFAULT 'approved' CHECK (approval IN ('pending', 'approved', 'denied'))")
			if'origin'not in columns:db.execute("ALTER TABLE fetch_jobs ADD COLUMN origin TEXT NOT NULL DEFAULT 'agent'")
		if not existed:self.path.chmod(384)
	def connect(self):db=sqlite3.connect(self.path,timeout=5);db.row_factory=sqlite3.Row;return db
	def note(self,note_id,text,at=None,acknowledged_at=None,ack_kind=None,ack_text=None,seen_at=None,task_id=None,ack_edited_at=None,autosave=True,shared=None,replies=None,ack_edited_seen_count=None,quiet=False):
		identifier(note_id);owner_text(text);receipt=restore_receipt(acknowledged_at,ack_kind,ack_text,ack_edited_at);more=restore_replies(replies,receipt[0]);seen_reply_count=restore_reply_seen_count(ack_edited_seen_count,more);seen=when(seen_at)if seen_at is not None else None
		with self.transaction(shared,autosave=autosave)as db:
			if shared is None:db.execute('BEGIN IMMEDIATE')
			existing=db.execute('SELECT * FROM notes WHERE id = ?',(note_id,)).fetchone()
			if existing:
				if existing['text']!=text:raise ValueError('This message ID already belongs to different text')
				return message_row(existing)
			db.execute('INSERT INTO notes (id, text, at, acknowledged_at, ack_kind, ack_text, ack_edited_at, seen_at, task_id, replies, ack_edited_seen_count, quiet) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',(note_id,text,at or now(),*receipt,seen,task_id,more,seen_reply_count,1 if quiet else 0));reset_poll_count(db);return message_row(db.execute('SELECT * FROM notes WHERE id = ?',(note_id,)).fetchone())
	def submission(self,submission_id,report_id,text,at=None,acknowledged_at=None,ack_kind=None,ack_text=None,seen_at=None,task_id=None,ack_edited_at=None,shared=None,autosave=True,replies=None,ack_edited_seen_count=None):
		identifier(submission_id);identifier(report_id);submission_text(text);receipt=restore_receipt(acknowledged_at,ack_kind,ack_text,ack_edited_at);more=restore_replies(replies,receipt[0]);seen_reply_count=restore_reply_seen_count(ack_edited_seen_count,more)
		with self.transaction(shared,autosave=autosave)as db:
			if shared is None:db.execute('BEGIN IMMEDIATE')
			existing=db.execute('SELECT * FROM submissions WHERE id = ?',(submission_id,)).fetchone()
			if existing:
				if existing['text']!=text:raise ValueError('This message ID already belongs to different text')
				return message_row(existing)
			db.execute('INSERT INTO submissions (id, report_id, text, at, acknowledged_at, ack_kind, ack_text, ack_edited_at, seen_at, task_id, replies, ack_edited_seen_count) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',(submission_id,report_id,text,at or now(),*receipt,when(seen_at)if seen_at is not None else None,task_id,more,seen_reply_count));reset_poll_count(db);return message_row(db.execute('SELECT * FROM submissions WHERE id = ?',(submission_id,)).fetchone())
	def submissions(self):
		with closing(self.connect())as db:return[message_row(row)for row in db.execute('SELECT * FROM submissions ORDER BY seq')]
	def state(self):
		tasks=self.tasks()
		with closing(self.connect())as db:
			meta=dict(db.execute('SELECT key, value FROM meta'));notes=[message_row(row)for row in db.execute('SELECT * FROM notes ORDER BY seq')];reports=[dict(row)for row in db.execute('SELECT id, title, updated_at, published_at, seq, seen_at, ever_seen, agent_seen_at, markdown, EXISTS(SELECT 1 FROM submissions WHERE report_id = reports.id) AS answered FROM reports ORDER BY seq, id')];latest_answers={row['report_id']:row for row in db.execute('SELECT * FROM submissions ORDER BY seq')}
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
				for key in('at','acknowledged_at','ack_edited_at','seen_at','updated_at','published_at'):
					if key in item:item[key]=clip_stamp(item[key])
				for reply in item.get('replies')or[]:reply['at']=clip_stamp(reply['at'])
			if tasks is not None:
				for item in tasks['finished']+tasks['upcoming']:item['updated_at']=clip_stamp(item['updated_at'])
				tasks['updated_at']=clip_stamp(tasks['updated_at'])
			return{'notes':notes,'reports':reports,'tasks':tasks,'uploads':uploads,'fetch_jobs':fetch_jobs,'workspace':workspace_usage(),'last_check':clip_stamp(meta.get('last_check')),'polling':self.polling(),'polling_since':clip_stamp(meta.get(POLL_SINCE_META))if self.polling()else None,'calls_since_message':meta_number(db,POLLS_SINCE_MESSAGE),'agent_key':self.agent_key()}
	def tasks(self):
		with closing(self.connect())as db:rows=db.execute(f"SELECT {TASK_COLUMNS} FROM tasks ORDER BY status DESC, position, id").fetchall()
		records=[task_row(row)for row in rows]
		if not records:return None
		return{'finished':[item for item in records if item['status']=='finished'],'upcoming':[item for item in records if item['status']=='upcoming'],'updated_at':max(item['updated_at']for item in records)}
	def list_tasks(self):
		with closing(self.connect())as db:rows=db.execute(f"SELECT {TASK_COLUMNS} FROM tasks ORDER BY status DESC, position, id").fetchall()
		return[task_row(row)for row in rows]
	@contextmanager
	def transaction(self,db=None,autosave=True):
		if db is not None:yield db;return
		with closing(self.connect())as own,own:yield own
		if autosave:self.autosave()
	def write_task(self,task_id,title=None,details=None,status=None,order=None,blocked=None,shared=None):
		details=[str(item)for item in details if str(item).strip()]if details else None;check_task(task_id,title,details)
		if status is not None and status not in TASK_STATUSES:raise ValueError(f"A task is either {' or '.join(TASK_STATUSES)}")
		stamp=now()
		with self.transaction(shared)as db:
			row=db.execute(f"SELECT {TASK_COLUMNS} FROM tasks WHERE id = ?",(task_id,)).fetchone();stored=task_row(row)if row else None
			if stored is None and title is None:raise ValueError('A new task needs a title')
			if stored is None:self.refuse_shared_id(db,'tasks','reports',task_id)
			title=title if title is not None else stored['title']
			if details is None:details=stored['details']if stored else[]
			status=status or(stored['status']if stored else'upcoming')
			if blocked is None:blocked=stored['blocked']if stored else False
			if status=='finished'and blocked:raise ValueError(f"Task {task_id} is blocked; clear the mark with --unblocked first")
			siblings=[task_row(item)for item in db.execute(f"SELECT {TASK_COLUMNS} FROM tasks WHERE status = ? AND id != ? ORDER BY position, id",(status,task_id)).fetchall()];record={'id':task_id,'title':title,'details':details,'status':status,'order':len(siblings)+1,'updated_at':stamp,'blocked':bool(blocked)}
			if order is not None:index=max(0,min(order-1,len(siblings)))
			elif stored and stored['status']==status:index=max(0,min(stored['order']-1,len(siblings)))
			else:index=len(siblings)
			siblings.insert(index,record);record['order']=index+1;db.execute('INSERT INTO tasks VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET title = excluded.title, details = excluded.details, status = excluded.status, position = excluded.position, updated_at = excluded.updated_at, blocked = excluded.blocked',(task_id,title,json.dumps(details,ensure_ascii=False),status,index+1,stamp,stamp,1 if blocked else 0))
			for(position,item)in enumerate(siblings,1):db.execute('UPDATE tasks SET position = ? WHERE id = ?',(position,item['id']))
			self.renumber(db)
		return record
	def remove_task(self,task_id):
		with self.transaction()as db:
			row=db.execute(f"SELECT {TASK_COLUMNS} FROM tasks WHERE id = ?",(task_id,)).fetchone()
			if row is None:raise ValueError(f"No task is stored under {task_id}")
			db.execute('DELETE FROM tasks WHERE id = ?',(task_id,));self.renumber(db)
		return task_row(row)
	def neighbours(self,task_id):
		with closing(self.connect())as db:
			row=db.execute('SELECT status, position FROM tasks WHERE id = ?',(task_id,)).fetchone()
			if row is None:return None,None
			status,position=row;before=db.execute('SELECT id FROM tasks WHERE status = ? AND position < ? ORDER BY position DESC, id DESC LIMIT 1',(status,position)).fetchone();after=db.execute('SELECT id FROM tasks WHERE status = ? AND position > ? ORDER BY position, id LIMIT 1',(status,position)).fetchone()
		return before[0]if before else None,after[0]if after else None
	@staticmethod
	def refuse_shared_id(db,table,other,identity):
		if db.execute(f"SELECT 1 FROM {other} WHERE id = ?",(identity,)).fetchone():raise ValueError(f"{identity} already names a {other[:-1]}; a {table[:-1]} needs another ID, for example {identity}-{table[:-1]}")
	def amend_task(self,prev_id,task_id):
		check_task(task_id,None,None);stamp=now()
		with self.transaction()as db:
			row=db.execute('SELECT id, title, details, status, position, created_at, blocked FROM tasks WHERE id = ?',(prev_id,)).fetchone()
			if row is None:raise ValueError(f"No task is stored under {prev_id}")
			if db.execute('SELECT 1 FROM tasks WHERE id = ?',(task_id,)).fetchone():raise ValueError(f"A task is already stored under {task_id}")
			self.refuse_shared_id(db,'tasks','reports',task_id);db.execute('DELETE FROM tasks WHERE id = ?',(prev_id,));db.execute('INSERT INTO tasks VALUES (?, ?, ?, ?, ?, ?, ?, ?)',(task_id,row[1],row[2],row[3],row[4],row[5],stamp,row[6]));self.renumber(db)
	def autosave(self):
		try:self.save_state({'notes':self.state()['notes'],'tasks':self.tasks()})
		except Exception as error:print(f"preview: autosave failed: {error}",file=sys.stderr)
	def save_state(self,payload):
		if not isinstance(payload,dict):raise TypeError('Save a state object')
		notes=payload.get('notes');tasks=payload.get('tasks')
		if tasks is None:tasks={}
		if not isinstance(notes,list)or not isinstance(tasks,dict):raise TypeError('Save a state object with notes and tasks')
		lines=[saved_note_line(record)for record in notes]
		for status in TASK_STATUSES:
			for record in tasks.get(status)or[]:lines.append(saved_task_line(record))
		answers=[saved_answer_line(record)for record in self.submissions()];lines.extend(answers);path=self.save_path
		if path.parent!=Path('.'):path.parent.mkdir(parents=True,exist_ok=True)
		path.write_text(''.join(json.dumps(line,ensure_ascii=False)+'\n'for line in lines),encoding='utf-8');return{'path':str(path),'notes':len(notes),'tasks':len(lines)-len(notes)-len(answers),'answers':len(answers)}
	def uploads(self):
		with closing(self.connect())as db:rows=db.execute('SELECT * FROM uploads ORDER BY seq').fetchall()
		return[upload_row(row,self.path.parent)for row in rows]
	def upload(self,upload_id):
		identifier(upload_id)
		with closing(self.connect())as db:row=db.execute('SELECT * FROM uploads WHERE id = ?',(upload_id,)).fetchone()
		return None if row is None else upload_row(row,self.path.parent)
	def save_upload(self,name,content_type,data,upload_id=None,shared=None,note_id=None,position=None):
		if not isinstance(data,(bytes,bytearray,FileBody)):raise TypeError('An upload is bytes')
		if not data:raise ValueError('An upload must not be empty')
		if len(data)>MAX_UPLOAD:raise ValueError(f"An upload must be {MAX_UPLOAD:,} bytes or fewer")
		cleaned=upload_name(name);kind=upload_type(content_type);digest=body_digest(data);upload_id=identifier(upload_id)if upload_id is not None else new_id();note_id=identifier(note_id)if note_id is not None else upload_id
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
				duplicate=f"-{number}"if number>1 else'';stem=f"{note_id[:7]}-{timestamp}{duplicate}-{filename_stem}";target=directory/f"{stem}{extension}"
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
			if not data or len(data)>MAX_UPLOAD:raise ValueError(f"Each attachment must be 1–{MAX_UPLOAD:,} bytes")
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
	def note_with_upload(self,note_id,text,name,content_type,data):return self.note_with_uploads(note_id,text,[(name,content_type,data)])
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
	def import_state(self,text,replace_tasks=False):
		records=parse_state_import(text);tasks=[r for r in records if'title'in r and'text'not in r];messages=[r for r in records if'title'not in r or'text'in r]
		with self.transaction(autosave=False)as db:
			db.execute('BEGIN IMMEDIATE');self.import_tasks(tasks,replace_tasks,autosave=False,shared=db)
			for record in messages:
				if'text'not in record or'title'in record:raise ValueError('Each record must be a note, task or report answer')
				options={key:record.get(key)for key in('acknowledged_at','ack_kind','ack_text','ack_edited_at','seen_at','task_id','replies','ack_edited_seen_count')};args=[record['id']];writer=self.note
				if'report_id'in record:writer=self.submission;args.append(record['report_id'])
				writer(*args,record['text'],record.get('at'),shared=db,autosave=False,**options)
		return{'notes':sum('report_id'not in r for r in messages),'answers':sum('report_id'in r for r in messages),'tasks':len(tasks)}
	def import_tasks(self,records,replace=False,autosave=True,shared=None):
		if not isinstance(records,list):raise TypeError('Import a list of task objects')
		prepared=[]
		for(index,record)in enumerate(records,1):
			if not isinstance(record,dict):raise TypeError('Import a list of task objects')
			details=[str(item)for item in record.get('details')or[]if str(item).strip()];status=record.get('status');check_task(record.get('id'),record.get('title'),details)
			if status is not None and status not in TASK_STATUSES:raise ValueError(f"A task is either {' or '.join(TASK_STATUSES)}")
			prepared.append((record.get('id'),record.get('title'),details,status,record.get('order')or index,record.get('blocked')))
		with self.transaction(shared,autosave=autosave)as db:
			if replace:
				for(task_id,title,_,_,_,_)in prepared:
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
	def set_meta(self,key,value):
		with closing(self.connect())as db,db:db.execute('INSERT OR REPLACE INTO meta VALUES (?, ?)',(key,str(value)))
	def set_agent_key(self,key,host=None):self.set_meta(AGENT_KEY_META,json.dumps({'key':key,'host':host,'at':now()},ensure_ascii=False))
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
	def reminder(self,advance=False):
		with closing(self.connect())as db,db:
			uploads=db.execute('SELECT count(*) FROM notes JOIN uploads USING (id) WHERE acknowledged_at IS NULL').fetchone()[0];notes=db.execute('SELECT count(*) FROM notes WHERE acknowledged_at IS NULL').fetchone()[0]-uploads;reports=db.execute('SELECT count(*) FROM submissions WHERE acknowledged_at IS NULL').fetchone()[0];remaining=db.execute("SELECT count(*) FROM tasks WHERE status <> 'finished'").fetchone()[0];cursor=meta_number(db,REMINDER_CURSOR);polls=0 if advance and not notes+reports+uploads else meta_number(db,POLLS_SINCE_MESSAGE)+(1 if advance else 0);db.execute('INSERT OR REPLACE INTO meta VALUES (?, ?)',(REMINDER_CURSOR,str(cursor+1)))
			if advance:db.execute('INSERT OR REPLACE INTO meta VALUES (?, ?)',(POLLS_SINCE_MESSAGE,str(polls)))
		counts=[f"{count} {kind}/s."for(count,kind)in((notes,'message'),(reports,'form answer'),(uploads,'upload'))if count];ack=['DO NOT IGNORE. ACK ASAP.']if counts else[];head=[f"{polls} call/s since user messaged."]if polls and counts else[];tail=reminder_tail(cursor,remaining);return' '.join([*head,*counts,*ack,tail])
	def gate(self,threshold=GATE_THRESHOLD,pending_only=False):
		with closing(self.connect())as db,db:pending=db.execute('SELECT (SELECT count(*) FROM notes WHERE acknowledged_at IS NULL AND quiet = 0) + (SELECT count(*) FROM submissions WHERE acknowledged_at IS NULL)').fetchone()[0];polls=meta_number(db,POLLS_SINCE_MESSAGE)
		if pending_only:return not pending
		return not(pending and polls>=threshold)
	def read(self,include_quiet=True):
		quiet_filter=''if include_quiet else' AND quiet = 0'
		with self.transaction()as db:
			pending=[dict(row)|{'kind':'note'}for row in db.execute(f"SELECT * FROM notes WHERE acknowledged_at IS NULL{quiet_filter} ORDER BY seq")];pending+=[dict(row)|{'kind':'report'}for row in db.execute('SELECT * FROM submissions WHERE acknowledged_at IS NULL ORDER BY seq')];attachments={}
			for row in db.execute('SELECT * FROM uploads ORDER BY seq'):record=upload_row(row,self.path.parent);attachments.setdefault(record['note_id'],[]).append(record)
			for item in pending:
				if item['kind']=='note':add_note_attachments(item,attachments.get(item['id'],[]))
				for key in('at','acknowledged_at','ack_edited_at','seen_at'):item[key]=clip_stamp(item[key])
			pending.sort(key=lambda item:item['at']);checked=now();db.execute("INSERT OR REPLACE INTO meta VALUES ('last_check', ?)",(checked,));return{'checked_at':clip_stamp(checked),'pending':pending}
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
			if not any(db.execute(f"SELECT 1 FROM {table} WHERE acknowledged_at IS NULL LIMIT 1").fetchone()for table in('notes','submissions')):db.execute("INSERT OR REPLACE INTO meta VALUES (?, '0')",(POLLS_SINCE_MESSAGE,))
	def publish(self,report_id,title,source):
		identifier(report_id)
		if not isinstance(title,str)or not title.strip()or len(title)>200:raise ValueError('Report title must contain 1–200 characters')
		source=Path(source)
		if source.suffix.lower()!='.md':raise ValueError('Publish a UTF-8 .md source file')
		with source.open('rb')as stream:data=stream.read(MAX_REPORT+1)
		if len(data)>MAX_REPORT:raise ValueError('Report exceeds the 2 MB limit; split it into reports')
		text=data.decode('utf-8');fields=parse_fields(text)[1]
		with self.transaction()as db:
			answered=db.execute('SELECT count(*) FROM submissions WHERE report_id = ?',(report_id,)).fetchone()[0]
			if answered:raise ValueError(f"Report {report_id} has submitted answers; publish the update under a new ID")
			self.refuse_shared_id(db,'reports','tasks',report_id);highest=db.execute('SELECT COALESCE(MAX(seq), 0) FROM reports').fetchone()[0];db.execute('INSERT INTO reports (id, title, markdown, updated_at, published_at, seq)\n           VALUES (?, ?, ?, ?, ?, ?)\n           ON CONFLICT(id) DO UPDATE SET title = excluded.title,\n             markdown = excluded.markdown, updated_at = excluded.updated_at,\n             seq = COALESCE(reports.seq, excluded.seq), seen_at = NULL',(report_id,title,text,now(),now(),highest+1))
		return len(fields)
	def unpublish(self,report_id):
		identifier(report_id)
		with self.transaction()as db:
			missing=db.execute('SELECT 1 FROM reports WHERE id = ?',(report_id,)).fetchone()
			if missing is None:raise FileNotFoundError('Report not found')
			db.execute('DELETE FROM reports WHERE id = ?',(report_id,))
	def mark_report_seen(self,report_id):
		identifier(report_id)
		with self.transaction()as db:
			row=db.execute('SELECT * FROM reports WHERE id = ?',(report_id,)).fetchone()
			if row is None:raise FileNotFoundError('Report not found')
			db.execute('UPDATE reports SET seen_at = COALESCE(seen_at, ?), ever_seen = 1 WHERE id = ?',(now(),report_id));return dict(db.execute('SELECT * FROM reports WHERE id = ?',(report_id,)).fetchone())
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
	if not HAS_RENDERER:raise SystemExit("serve needs markdown-it-py: install it into the preview venv with `python -m pip install markdown-it-py`, then start the server with that venv's Python. read, ack and publish work without it.")
CODE_BLOCK=re.compile('<pre>(.*?)</pre>',re.DOTALL)
CODE_TAG=re.compile('<[^>]+>')
def add_copy_buttons(rendered):
	def replace(match):inner=match.group(1);code=html.unescape(CODE_TAG.sub('',inner));payload=html.escape(code,quote=True).replace('\n','&#10;');button=f'<button type="button" class="copy-code" aria-label="Copy code" title="Copy code" data-code="{payload}">⧉</button>';return f'<div class="code-block">{button}<pre>{inner}</pre></div>'
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
			if filename:safe=re.sub('[\\r\\n"]','_',str(filename));self.send_header('Content-Disposition',f'attachment; filename="{safe}"')
			self.end_headers();self.wfile.write(data)
		def problem(self,status,error):self.reply(status,json.dumps({'error':str(error)}))
		def do_GET(self):
			path=urlsplit(self.path).path
			try:
				if path=='/':page=(ASSETS/'index.html').read_text(encoding='utf-8');page=page.replace('__STYLE__',(ASSETS/'style.css').read_text(encoding='utf-8'));page=page.replace('__SCRIPT__',(ASSETS/'app.js').read_text(encoding='utf-8'));self.reply(200,page.replace('__TOKEN__',token),'text/html; charset=utf-8');return
				if path=='/api/probe':self.reply(200,json.dumps({'ok':True,'route':'/api/probe','from':self.client_address[0],'at':datetime.now(timezone.utc).isoformat(timespec='seconds'),'hint':'POST a JSON body to echo it'},indent=2)+'\n');return
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
				upload=re.fullmatch('/api/uploads/([a-zA-Z0-9_-]{1,80})',path)
				if upload:
					record=store.upload(upload.group(1))
					if record is None:self.problem(404,'No upload with that ID');return
					try:data=Path(record['path']).read_bytes()
					except OSError:self.problem(404,"This upload's bytes are gone; the record survived a restore");return
					self.reply(200,data,record['type'],record['name']);return
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
			path=urlsplit(self.path).path;report_submit=re.fullmatch('/api/reports/([a-zA-Z0-9_-]{1,80})/submit',path);report_seen=re.fullmatch('/api/reports/([a-zA-Z0-9_-]{1,80})/seen',path);message_replies_seen=re.fullmatch('/api/messages/([a-zA-Z0-9_-]{1,80})/replies/seen',path);report_unpublish=re.fullmatch('/api/reports/([a-zA-Z0-9_-]{1,80})/unpublish',path);fetch_post=re.fullmatch('/api/fetch-jobs/([a-zA-Z0-9_-]{1,80})/(renew|result|fail|retry|approve|deny)',path);upload_post=path=='/api/uploads';note_upload=path=='/api/notes/with-file';probe=path=='/api/probe';agent_key_post=path=='/api/key';fetch_result=bool(fetch_post and fetch_post.group(2)=='result')
			if path not in{'/api/notes','/api/markdown','/api/fetch-jobs','/api/fetch-jobs/claim'}and not report_submit and not report_seen and not message_replies_seen and not report_unpublish and not upload_post and not note_upload and not fetch_post and not probe and not agent_key_post:self.problem(404,'Not found');return
			if probe:
				length=int(self.headers.get('Content-Length','0')or 0);raw=self.rfile.read(length)if length else b''
				try:body=json.loads(raw.decode('utf-8'))if raw else{}
				except(UnicodeDecodeError,json.JSONDecodeError):self.problem(400,'the probe body must be JSON');return
				self.reply(200,json.dumps({'ok':True,'route':'/api/probe','from':self.client_address[0],'at':datetime.now(timezone.utc).isoformat(timespec='seconds'),'echo':body},indent=2)+'\n');return
			content_type=self.headers.get('Content-Type','')
			if note_upload:
				if not content_type.lower().startswith('multipart/form-data;'):self.problem(415,'Expected multipart/form-data');return
			elif content_type!='application/json'and not(upload_post or fetch_result):self.problem(415,'Expected application/json');return
			try:
				if self.headers.get('Transfer-Encoding')or len(self.headers.get_all('Content-Length',[]))!=1:raise ValueError('Send one Content-Length and no Transfer-Encoding')
				length=int(self.headers.get('Content-Length','0'));limit=MAX_UPLOAD if upload_post else None
				if fetch_result:
					with closing(store.connect())as db:job=store.claimed_fetch(db,fetch_post.group(1),self.headers.get('X-Fetch-Claim',''));limit=MAX_FETCH if job['origin']!='owner'else None
				if length<=0 or limit is not None and length>limit:
					subject='Upload'if upload_post or note_upload else'Download'if fetch_result else'Request body';remaining=length if limit is not None and 0<length<=limit+1 else 0
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
				if upload_post:name=parse_qs(urlsplit(self.path).query).get('name',[''])[0];record=store.save_upload(name,self.headers.get('Content-Type',''),data);record['at']=clip_stamp(record['at']);store.note(record['id'],f"Upload: {record['name']} ({record['size']} B, {record['type']or'unknown type'}) saved to {record['path']}");self.reply(201,json.dumps(record,ensure_ascii=False));return
				payload=json.loads(data)
				if not isinstance(payload,dict):self.problem(400,'Expected a JSON object');return
				if agent_key_post:
					candidate=payload.get('key')
					if not isinstance(candidate,str)or not AGENT_KEY_RE.fullmatch(candidate):raise ValueError('key must be 20 to 64 letters, digits, dashes or underscores')
					host=payload.get('host')
					if host is not None and(not isinstance(host,str)or not AGENT_HOST_RE.fullmatch(host)):raise ValueError('host must be one https origin, with no path')
					store.set_agent_key(candidate,host);self.reply(200,json.dumps(store.agent_key(),ensure_ascii=False));return
				if path=='/api/fetch-jobs':record=store.enqueue_fetch(payload.get('url'),payload.get('allow_proxy',False));self.reply(201,json.dumps(record,ensure_ascii=False));return
				if path=='/api/fetch-jobs/claim':self.reply(200,json.dumps({'job':store.claim_fetch()},ensure_ascii=False));return
				if fetch_post:
					job_id,action=fetch_post.groups()
					if action=='renew':record=store.renew_fetch(job_id,self.headers.get('X-Fetch-Claim',''))
					elif action=='fail':record=store.fail_fetch(job_id,self.headers.get('X-Fetch-Claim',''),payload.get('error',''))
					elif action in{'approve','deny'}:record=store.decide_fetch(job_id,'approved'if action=='approve'else'denied')
					else:record=store.retry_fetch(job_id)
					self.reply(200,json.dumps(record,ensure_ascii=False));return
				if path=='/api/markdown':self.reply(200,render(owner_text(payload.get('text')),breaks=True),'text/html; charset=utf-8');return
				if report_seen:
					report=store.mark_report_seen(report_seen.group(1))
					for key in('updated_at','seen_at'):report[key]=clip_stamp(report[key])
					self.reply(200,json.dumps(report,ensure_ascii=False));return
				if message_replies_seen:seen=store.mark_replies_seen(message_replies_seen.group(1),payload.get('count'));self.reply(200,json.dumps(seen,ensure_ascii=False));return
				if report_unpublish:store.unpublish(report_unpublish.group(1));self.reply(200,json.dumps({'unpublished':report_unpublish.group(1)}));return
				if report_submit:
					note=store.submit_report(report_submit.group(1),payload.get('id'),payload.get('answers'),payload.get('revision'))
					for key in('at','acknowledged_at','ack_edited_at','seen_at'):note[key]=clip_stamp(note[key])
					self.reply(201,json.dumps(note,ensure_ascii=False));return
				note=store.note(payload.get('id'),payload.get('text'),quiet=bool(payload.get('quiet')))
				for key in('at','acknowledged_at','ack_edited_at','seen_at'):note[key]=clip_stamp(note[key])
				self.reply(201,json.dumps(note,ensure_ascii=False))
			except(ReportChanged,FetchChanged)as error:self.problem(409,error)
			except FileNotFoundError as error:self.problem(404,error)
			except(ValueError,TypeError,UnicodeDecodeError)as error:self.problem(400,error)
			except(OSError,sqlite3.Error,RuntimeError)as error:self.problem(503,error)
	return Handler
def resolve_state_dir():
	configured=os.environ.get('ARENA_PREVIEW_STATE_DIR')
	if configured:return configured
	for parent in Path(__file__).resolve().parents:
		if(parent/'.git').exists():return str(parent/'arena-state')
	return'arena-state'
def main():
	parser=argparse.ArgumentParser(description=CLI_DESCRIPTION);parser.add_argument('--reminder',action='store_true',help='Print the unacked-count reminder line and exit');parser.add_argument('--pretty',action='store_true',help='Indent the JSON this CLI prints; agent-facing output is minified by default');commands=parser.add_subparsers(dest='command',required=False);serve=commands.add_parser('serve');serve.add_argument('--port',type=int,default=8000,help='Port to bind (default: 8000)');commands.add_parser('init');commands.add_parser('read');commands.add_parser('key');gate=commands.add_parser('gate');gate.add_argument('--push',action='store_true',help='Block while any note or answer awaits an ack, whatever the call count');commands.add_parser('poll');download=commands.add_parser('download-request',help='Request an HTTPS browser download, pending a preview Approve click');download.add_argument('url',help='One HTTPS URL without embedded credentials');download.add_argument('--allow-proxy',action='store_true',help='Let the owner opt in to AllOrigins and CodeTabs fallback for this request');ack=commands.add_parser('ack');ack.add_argument('ids',nargs='+');ack.add_argument('--reply',help='Markdown answer shown in the message log');ack.add_argument('--note',help='Short plain answer shown in the message log');publish=commands.add_parser('publish');publish.add_argument('source',type=Path);publish.add_argument('--id',required=True);publish.add_argument('--title',required=True);unpublish=commands.add_parser('unpublish');unpublish.add_argument('report_id');task=commands.add_parser('task');task.add_argument('id_arg',nargs='?',metavar='TASK-ID');task.add_argument('title_arg',nargs='?',metavar='TASK-TITLE');task.add_argument('detail_arg',nargs='*',metavar='TASK-DETAIL');task.add_argument('--task-details',action='append',help='One detail line, repeatable; an empty string clears the list');task.add_argument('--blocked',dest='blocked',action='store_true',default=None,help='Mark the task blocked, so a poll may wait');task.add_argument('--unblocked',dest='blocked',action='store_false',help='Clear the blocked mark');task.add_argument('--msg-id',help='Message this task answers; marks that message as having a task');task.add_argument('--amend',metavar='PREV-ID',help='Rename the task stored under this ID to the one given');task.add_argument('--status',choices=TASK_STATUSES,default=None);task.add_argument('--order',type=int,default=None,help='1-based place in its div, not the end');task_remove=commands.add_parser('task-remove');task_remove.add_argument('task_id');commands.add_parser('task-list');state_import=commands.add_parser('import-state');state_import.add_argument('source',nargs='?',type=Path);state_import.add_argument('--replace-tasks',action='store_true');args=parser.parse_args();state_dir=resolve_state_dir()
	try:
		if args.reminder:store=Store(state_dir,create=False);require_server(store);print(store.reminder(advance=True),flush=True);return 0
		if not args.command:parser.error('a command is required')
		if args.command=='gate':
			try:allowed=Store(state_dir).gate(pending_only=args.push)
			except FileNotFoundError:allowed=True
			except Exception:return 2
			if not allowed:print('READ INBOX NOW WITH `arena-preview read`, THEN ACK EVERY NOTE WITH `arena-preview ack <id>`',flush=True);return 1
			if args.push and main_identical():print('HEAD content equals `origin/main`, so the push carries nothing. Start new work from `origin/main`.',flush=True);return 1
			return 0
		store=Store(state_dir,create=args.command in{'serve','init','import-state'});print(store.reminder(),file=sys.stderr,flush=True)
		if args.command=='serve':
			require_renderer()
			with ThreadingHTTPServer(('0.0.0.0',args.port),handler(store))as server:store.set_meta('port',str(server.server_port));print(f"Preview listening on 0.0.0.0:{server.server_port}; state: {store.path}",flush=True);server.serve_forever()
		elif args.command=='read':require_server(store);print_read(store,args.pretty)
		elif args.command=='key':
			record=store.agent_key()
			if not record:print('No agent key recorded yet.',file=sys.stderr);return 1
			print(cli_json(record,args.pretty))
		elif args.command=='poll':require_server(store);return poll_inbox(store,args.pretty)
		elif args.command=='download-request':print(cli_json(store.enqueue_fetch(args.url,args.allow_proxy,pending=True),args.pretty))
		elif args.command=='ack':
			if bool(args.reply)==bool(args.note):raise ValueError('Choose exactly one of --reply or --note')
			kind='reply'if args.reply else'note';store.acknowledge(args.ids,kind,args.reply or args.note);print('Acknowledged: '+', '.join(args.ids));print('If a note asks for work, add it to the task list: '+'; '.join(f'task <id> "<title>" --msg-id {i}'for i in args.ids))
		elif args.command=='publish':
			count=store.publish(args.id,args.title,args.source);print(f"Published {args.id} with {count} fields; select it in the Reports tab")
			if not count and'{#'in Path(args.source).read_text('utf-8'):print('Warning: 0 fields parsed; a `{#id}` marker ends a prompt line and the `- ( ) option` lines follow it',file=sys.stderr)
		elif args.command=='unpublish':store.unpublish(args.report_id);print(f"Unpublished {args.report_id}; its answers and source file remain")
		elif args.command=='task':
			task_id=args.id_arg
			if not task_id:raise ValueError('A task needs an ID')
			details=args.task_details
			if details is None and args.detail_arg:details=args.detail_arg
			if args.amend:store.amend_task(args.amend,task_id)
			if args.msg_id:
				with store.transaction()as shared:record=store.write_task(task_id,args.title_arg,details,args.status,args.order,args.blocked,shared=shared);store.mark_task(args.msg_id,task_id,shared=shared)
			else:record=store.write_task(task_id,args.title_arg,details,args.status,args.order,args.blocked)
			before,after=store.neighbours(task_id);echo=echo_task(record,before,after)
			if args.msg_id:echo['msg_id']=args.msg_id
			print(cli_json(echo,args.pretty))
		elif args.command=='task-remove':print(cli_json(echo_task(store.remove_task(args.task_id)),args.pretty))
		elif args.command=='task-list':print(cli_json(store.list_tasks(),args.pretty))
		elif args.command=='import-state':text=args.source.read_text(encoding='utf-8')if args.source else sys.stdin.read();print(cli_json(store.import_state(text,args.replace_tasks),args.pretty))
	except(OSError,ValueError,TypeError,KeyError,sqlite3.Error,RuntimeError)as error:print(f"Preview error: {error}",file=sys.stderr);return 1
	return 0
if __name__=='__main__':raise SystemExit(main())
