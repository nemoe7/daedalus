import argparse
import hashlib
import html
import json
import os
import re
import secrets
import socket
import sqlite3
import sys
import uuid
from contextlib import closing,contextmanager
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
MAX_SUBMISSION=150000
MAX_BODY=96000
MAX_SUBMISSION_BODY=1000000
MAX_UPLOAD=50000000
MAX_ATTACHMENTS=5
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
REMINDERS='Manage the task list.','Take the smallest open task next.','Always push.','Report GH_TOKEN failure; use ask_user only if requested or preview unavailable.','Keep docs terse but clear.','Ask questions ASAP through fielded reports; keep other work moving.',"Don't forget to publish your reports.",'Avoid ending turn if there are unblocked tasks.'
REMINDER_CURSOR='reminder_cursor'
POLLS_SINCE_MESSAGE='polls_since_message'
def now():return datetime.now(timezone.utc).isoformat()
def reset_poll_count(db):
	unacked=db.execute('SELECT (SELECT count(*) FROM notes WHERE acknowledged_at IS NULL) + (SELECT count(*) FROM submissions WHERE acknowledged_at IS NULL)').fetchone()[0]
	if unacked==1:db.execute("INSERT OR REPLACE INTO meta VALUES (?, '0')",(POLLS_SINCE_MESSAGE,))
def meta_number(db,key):row=db.execute('SELECT value FROM meta WHERE key = ?',(key,)).fetchone();value=str(row[0])if row else'';return int(value)if value.isdigit()else 0
def new_id():hexed=uuid.uuid4().hex;return f"{hexed[:7]}-{hexed[7:]}"
def clip_stamp(value):return value[:19]if value else value
def identifier(value):
	if not isinstance(value,str)or not IDENTIFIER.fullmatch(value):raise ValueError('ID must contain 1–80 letters, digits, underscores or hyphens')
	return value
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
def submission_text(text):
	if not isinstance(text,str)or not text.strip():raise ValueError('A report submission carries at least one answer')
	if len(text)>MAX_SUBMISSION:raise ValueError(f"A report submission must be under {MAX_SUBMISSION:,} characters; answer fewer fields or shorten them")
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
		if typed.strip()and len(typed)<=2000:return True
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
	if question['type']=='text':return f'{body}<textarea class="answer-text" rows="2" maxlength="2000" placeholder="Answer" aria-label="{prompt}"></textarea></div>'
	control='radio'if question['type']=='choice'else'checkbox';group=f'<div class="options" role="group" aria-label="{prompt}">'
	for option in question['options']:
		value=html.escape(option,quote=True);checked=' checked'if option in question['default']else'';label=custom_label(option)
		if label is None:group+=f'<label class="option"><input type="{control}" name="{name}" value="{value}"{checked}> {html.escape(option)}</label>';continue
		named=html.escape(label,quote=True);group+=f'<label class="option"><input type="{control}" name="{name}" value="{value}"{checked} data-label="{named}" aria-label="{named}"><textarea class="custom-text" rows="1" maxlength="2000" data-custom="{named}" placeholder="{named}:" aria-label="{named}, your own answer"></textarea></label>'
	return f"{body}{group}</div></div>"
TASK_STATUSES='upcoming','finished'
TASK_ID=re.compile('^[a-z0-9][a-z0-9-]{0,63}$')
MAX_TASK_TITLE=200
MAX_TASK_DETAIL=2000
MAX_TASK_DETAILS=40
ECHO_DETAIL=200
TASK_COLUMNS='id, title, details, status, position, updated_at'
CLI_DESCRIPTION='Notes, reports, tasks and the preview server for Arena steering.'
SAVED_STATE='saved-state.ndjson'
NOTE_LINE_KEYS='id','text','at','acknowledged_at','ack_kind','ack_text','ack_edited_at','replies','seen_at','task_id'
TASK_LINE_KEYS='id','title','details','status','order'
SUBMISSION_LINE_KEYS='id','report_id','text','at','acknowledged_at','ack_kind','ack_text','ack_edited_at','replies','seen_at','task_id'
def task_row(row):return{'id':row[0],'title':row[1],'details':json.loads(row[2]),'status':row[3],'order':row[4],'updated_at':row[5]}
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
	if len(cleaned)>200:raise ValueError('A file name must be 200 characters or fewer')
	if any(ord(char)<32 or ord(char)==127 for char in cleaned):raise ValueError('A file name must not contain control characters')
	return cleaned
def fetch_url(value):
	if not isinstance(value,str)or not value.strip()or len(value)>2048:raise ValueError('Enter one HTTPS URL of at most 2048 characters')
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
def parse_note_attachments(content_type,data):
	if len(content_type)>200 or any(char in content_type for char in'\r\n'):raise ValueError('Invalid multipart boundary')
	prefix=b'MIME-Version: 1.0\r\nContent-Type: '+content_type.encode('ascii')+b'\r\n\r\n';message=BytesParser(policy=policy.default).parsebytes(prefix+data)
	if not message.is_multipart()or message.defects:raise ValueError('Send files with a valid multipart boundary')
	parts=list(message.iter_parts())
	if not 3<=len(parts)<=MAX_ATTACHMENTS+2:raise ValueError(f"Send one note ID, text and 1–{MAX_ATTACHMENTS} files")
	fields,files={},[]
	for part in parts:
		name=part.get_param('name',header='content-disposition')
		if part.get_content_disposition()!='form-data'or name not in{'id','text','file'}:raise ValueError('Unexpected note attachment field')
		if part.defects or part.is_multipart()or name!='file'and name in fields:raise ValueError('Duplicate or invalid note attachment field')
		body=part.get_payload(decode=True)
		if body is None:raise ValueError('Invalid note attachment bytes')
		if name=='file':files.append((part.get_filename(),part.get_content_type(),body))
		else:
			if part.get_filename()is not None:raise ValueError('Only file may have a filename')
			fields[name]=body.decode('utf-8')
	if set(fields)!={'id','text'}or not files:raise ValueError('Send one note ID, text and at least one file')
	return fields['id'],fields['text'],files
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
	raise ValueError('preview server is down; start it again before polling')
def print_read(store,pretty=False):listing=store.read();print(cli_json(listing,pretty),flush=True);store.mark_seen([item['id']for item in listing['pending']])
def parse_task_import(text):
	stripped=text.strip()
	if not stripped:raise ValueError('Nothing to import')
	records=json.loads(stripped)if stripped.startswith('[')else[json.loads(line)for line in stripped.splitlines()if line.strip()]
	if not isinstance(records,list)or not all(isinstance(item,dict)for item in records):raise ValueError('Import a JSON array of task objects, or one task object per line')
	return records
def check_task(task_id,title,details):
	if not TASK_ID.match(task_id or''):raise ValueError('A task ID is 1-64 characters of lowercase letters, digits and hyphens, and starts with a letter or digit')
	if title is not None and len(title)>MAX_TASK_TITLE:raise ValueError(f"A task title must be {MAX_TASK_TITLE} characters or fewer")
	if len(details or())>MAX_TASK_DETAILS:raise ValueError(f"A task carries at most {MAX_TASK_DETAILS} details")
	for detail in details or():
		if len(detail)>MAX_TASK_DETAIL:raise ValueError(f"A task detail must be {MAX_TASK_DETAIL} characters or fewer")
REPORT_LIST_TAG=re.compile('\\s*</?(?:ul|ol|li)>\\s*',re.IGNORECASE)
REPORT_LIST_TAGS=re.compile('^(?:\\s*</?(?:ul|ol|li)>\\s*)+$',re.IGNORECASE)
def render_report_block(markdown):
	held=[]
	def hold(match):held.append(match.group(0).strip().lower());return f" previewlisttag{len(held)-1}x "
	tagged=re.sub('(?:\\s*(?:</?(?:ul|ol|li)>)\\s*){2,}',hold,markdown);rendered=render(tagged)
	def restore(match):return held[int(match.group(1))]
	out=re.sub('previewlisttag(\\d+)x',restore,rendered);out=re.sub('<p>(<(?:ul|ol)>)','\\1',out);out=re.sub('(</(?:ul|ol)>)</p>','\\1',out);return out
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
		if not create and not existed:raise FileNotFoundError(f"Inbox missing: {self.path}; start the preview first")
		if create and not existed:directory.mkdir(parents=True,exist_ok=True,mode=448)
		with closing(self.connect())as db,db:
			db.executescript("\n        CREATE TABLE IF NOT EXISTS notes (\n          seq INTEGER PRIMARY KEY, id TEXT UNIQUE NOT NULL,\n          text TEXT NOT NULL, at TEXT NOT NULL, acknowledged_at TEXT,\n          ack_kind TEXT, ack_text TEXT, seen_at TEXT, replies TEXT\n        );\n        CREATE TABLE IF NOT EXISTS reports (\n          id TEXT PRIMARY KEY, title TEXT NOT NULL,\n          markdown TEXT NOT NULL, updated_at TEXT NOT NULL, seq INTEGER, seen_at TEXT\n        );\n        CREATE TABLE IF NOT EXISTS submissions (\n          seq INTEGER PRIMARY KEY, id TEXT UNIQUE NOT NULL,\n          report_id TEXT NOT NULL, text TEXT NOT NULL, at TEXT NOT NULL,\n          acknowledged_at TEXT, ack_kind TEXT, ack_text TEXT, seen_at TEXT, replies TEXT\n        );\n        CREATE TABLE IF NOT EXISTS uploads (\n          seq INTEGER PRIMARY KEY, id TEXT UNIQUE NOT NULL, note_id TEXT,\n          name TEXT NOT NULL, type TEXT NOT NULL, size INTEGER NOT NULL,\n          sha256 TEXT NOT NULL, file TEXT NOT NULL, at TEXT NOT NULL\n        );\n        CREATE TABLE IF NOT EXISTS fetch_jobs (\n          seq INTEGER PRIMARY KEY, id TEXT UNIQUE NOT NULL,\n          url TEXT NOT NULL, allow_proxy INTEGER NOT NULL CHECK (allow_proxy IN (0, 1)),\n          status TEXT NOT NULL CHECK (status IN ('queued', 'fetching', 'saved', 'failed')),\n          approval TEXT NOT NULL DEFAULT 'approved'\n            CHECK (approval IN ('pending', 'approved', 'denied')),\n          claim TEXT, lease_until TEXT, error TEXT, source TEXT,\n          name TEXT, type TEXT, size INTEGER, sha256 TEXT, file TEXT,\n          at TEXT NOT NULL, updated_at TEXT NOT NULL\n        );\n        CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);\n        CREATE TABLE IF NOT EXISTS tasks (\n          id TEXT PRIMARY KEY,\n          title TEXT NOT NULL,\n          details TEXT NOT NULL DEFAULT '[]',\n          status TEXT NOT NULL DEFAULT 'upcoming'\n            CHECK (status IN ('upcoming', 'finished')),\n          position INTEGER NOT NULL,\n          created_at TEXT NOT NULL,\n          updated_at TEXT NOT NULL\n        );\n      ");columns={row['name']for row in db.execute('PRAGMA table_info(notes)')}
			for column in('ack_kind','ack_text','ack_edited_at','seen_at','task_id','replies'):
				if column not in columns:db.execute(f"ALTER TABLE notes ADD COLUMN {column} TEXT")
			if'origin'in columns:db.execute('ALTER TABLE notes DROP COLUMN origin');columns.discard('origin')
			if'seen_at'not in columns:db.execute('UPDATE notes SET seen_at = acknowledged_at WHERE seen_at IS NULL AND acknowledged_at IS NOT NULL')
			columns={row['name']for row in db.execute('PRAGMA table_info(submissions)')}
			if'ack_edited_at'not in columns:db.execute('ALTER TABLE submissions ADD COLUMN ack_edited_at TEXT')
			if'seen_at'not in columns:db.execute('ALTER TABLE submissions ADD COLUMN seen_at TEXT')
			if'task_id'not in columns:db.execute('ALTER TABLE submissions ADD COLUMN task_id TEXT')
			if'replies'not in columns:db.execute('ALTER TABLE submissions ADD COLUMN replies TEXT')
			columns={row['name']for row in db.execute('PRAGMA table_info(reports)')}
			if'seen_at'not in columns:db.execute('ALTER TABLE reports ADD COLUMN seen_at TEXT');db.execute('UPDATE submissions SET seen_at = acknowledged_at WHERE seen_at IS NULL AND acknowledged_at IS NOT NULL')
			columns={row['name']for row in db.execute('PRAGMA table_info(reports)')}
			if'seq'not in columns:db.execute('ALTER TABLE reports ADD COLUMN seq INTEGER');db.execute('UPDATE reports SET seq = rowid WHERE seq IS NULL')
			columns={row['name']for row in db.execute('PRAGMA table_info(uploads)')}
			if'note_id'not in columns:db.execute('ALTER TABLE uploads ADD COLUMN note_id TEXT');db.execute('UPDATE uploads SET note_id = id WHERE note_id IS NULL')
			db.execute('CREATE INDEX IF NOT EXISTS uploads_note_id ON uploads(note_id)');columns={row['name']for row in db.execute('PRAGMA table_info(fetch_jobs)')}
			if'approval'not in columns:db.execute("ALTER TABLE fetch_jobs ADD COLUMN approval TEXT NOT NULL DEFAULT 'approved' CHECK (approval IN ('pending', 'approved', 'denied'))")
		if not existed:self.path.chmod(384)
	def connect(self):db=sqlite3.connect(self.path,timeout=5);db.row_factory=sqlite3.Row;return db
	def note(self,note_id,text,at=None,acknowledged_at=None,ack_kind=None,ack_text=None,seen_at=None,task_id=None,ack_edited_at=None,autosave=True,shared=None,replies=None):
		identifier(note_id);note_text(text);receipt=restore_receipt(acknowledged_at,ack_kind,ack_text,ack_edited_at);more=restore_replies(replies,receipt[0]);seen=when(seen_at)if seen_at is not None else None
		with self.transaction(shared,autosave=autosave)as db:
			if shared is None:db.execute('BEGIN IMMEDIATE')
			existing=db.execute('SELECT * FROM notes WHERE id = ?',(note_id,)).fetchone()
			if existing:
				if existing['text']!=text:raise ValueError('This message ID already belongs to different text')
				return message_row(existing)
			db.execute('INSERT INTO notes (id, text, at, acknowledged_at, ack_kind, ack_text, ack_edited_at, seen_at, task_id, replies) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',(note_id,text,at or now(),*receipt,seen,task_id,more));reset_poll_count(db);return message_row(db.execute('SELECT * FROM notes WHERE id = ?',(note_id,)).fetchone())
	def submission(self,submission_id,report_id,text,at=None,acknowledged_at=None,ack_kind=None,ack_text=None,seen_at=None,task_id=None,ack_edited_at=None,shared=None,autosave=True,replies=None):
		identifier(submission_id);identifier(report_id);submission_text(text);receipt=restore_receipt(acknowledged_at,ack_kind,ack_text,ack_edited_at);more=restore_replies(replies,receipt[0])
		with self.transaction(shared,autosave=autosave)as db:
			if shared is None:db.execute('BEGIN IMMEDIATE')
			existing=db.execute('SELECT * FROM submissions WHERE id = ?',(submission_id,)).fetchone()
			if existing:
				if existing['text']!=text:raise ValueError('This message ID already belongs to different text')
				return message_row(existing)
			db.execute('INSERT INTO submissions (id, report_id, text, at, acknowledged_at, ack_kind, ack_text, ack_edited_at, seen_at, task_id, replies) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',(submission_id,report_id,text,at or now(),*receipt,when(seen_at)if seen_at is not None else None,task_id,more));reset_poll_count(db);return message_row(db.execute('SELECT * FROM submissions WHERE id = ?',(submission_id,)).fetchone())
	def submissions(self):
		with closing(self.connect())as db:return[message_row(row)for row in db.execute('SELECT * FROM submissions ORDER BY seq')]
	def state(self):
		tasks=self.tasks()
		with closing(self.connect())as db:
			meta=dict(db.execute('SELECT key, value FROM meta'));notes=[message_row(row)for row in db.execute('SELECT * FROM notes ORDER BY seq')];reports=[dict(row)for row in db.execute('SELECT id, title, updated_at, seq, seen_at, markdown, EXISTS(SELECT 1 FROM submissions WHERE report_id = reports.id) AS answered FROM reports ORDER BY seq, id')];latest_answers={row['report_id']:row for row in db.execute('SELECT id, report_id, at, acknowledged_at FROM submissions ORDER BY seq')}
			for report in reports:
				answered=report.pop('answered');latest=latest_answers.get(report['id']);report['latest_answer_id']=latest['id']if latest else None;report['latest_answer_at']=clip_stamp(latest['at'])if latest else None;report['latest_answer_acknowledged_at']=clip_stamp(latest['acknowledged_at'])if latest else None
				try:report['needs_answer']=bool(parse_fields(report.pop('markdown'))[1])and not answered
				except ValueError as error:report['needs_answer']=True;report['field_error']=str(error)
			uploads=self.uploads();by_note={}
			for item in uploads:by_note.setdefault(item['note_id'],[]).append(item)
			for note in notes:add_note_attachments(note,by_note.get(note['id'],[]))
			fetch_jobs=self.fetch_jobs()
			for item in notes+reports+uploads+fetch_jobs:
				for key in('at','acknowledged_at','ack_edited_at','seen_at','updated_at'):
					if key in item:item[key]=clip_stamp(item[key])
				for reply in item.get('replies')or[]:reply['at']=clip_stamp(reply['at'])
			if tasks is not None:
				for item in tasks['finished']+tasks['upcoming']:item['updated_at']=clip_stamp(item['updated_at'])
				tasks['updated_at']=clip_stamp(tasks['updated_at'])
			return{'notes':notes,'reports':reports,'tasks':tasks,'uploads':uploads,'fetch_jobs':fetch_jobs,'workspace':workspace_usage(),'last_check':clip_stamp(meta.get('last_check'))}
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
	def write_task(self,task_id,title=None,details=None,status=None,order=None,shared=None):
		details=[str(item)for item in details if str(item).strip()]if details else None;check_task(task_id,title,details)
		if status is not None and status not in TASK_STATUSES:raise ValueError(f"A task is either {' or '.join(TASK_STATUSES)}")
		stamp=now()
		with self.transaction(shared)as db:
			row=db.execute(f"SELECT {TASK_COLUMNS} FROM tasks WHERE id = ?",(task_id,)).fetchone();stored=task_row(row)if row else None
			if stored is None and title is None:raise ValueError('A new task needs a title')
			title=title if title is not None else stored['title']
			if details is None:details=stored['details']if stored else[]
			status=status or(stored['status']if stored else'upcoming');siblings=[task_row(item)for item in db.execute(f"SELECT {TASK_COLUMNS} FROM tasks WHERE status = ? AND id != ? ORDER BY position, id",(status,task_id)).fetchall()];record={'id':task_id,'title':title,'details':details,'status':status,'order':len(siblings)+1,'updated_at':stamp}
			if order is not None:index=max(0,min(order-1,len(siblings)))
			elif stored and stored['status']==status:index=max(0,min(stored['order']-1,len(siblings)))
			else:index=len(siblings)
			siblings.insert(index,record);record['order']=index+1;db.execute('INSERT INTO tasks VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET title = excluded.title, details = excluded.details, status = excluded.status, position = excluded.position, updated_at = excluded.updated_at',(task_id,title,json.dumps(details,ensure_ascii=False),status,index+1,stamp,stamp))
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
	def amend_task(self,prev_id,task_id):
		check_task(task_id,None,None);stamp=now()
		with self.transaction()as db:
			row=db.execute('SELECT id, title, details, status, position, created_at FROM tasks WHERE id = ?',(prev_id,)).fetchone()
			if row is None:raise ValueError(f"No task is stored under {prev_id}")
			if db.execute('SELECT 1 FROM tasks WHERE id = ?',(task_id,)).fetchone():raise ValueError(f"A task is already stored under {task_id}")
			db.execute('DELETE FROM tasks WHERE id = ?',(prev_id,));db.execute('INSERT INTO tasks VALUES (?, ?, ?, ?, ?, ?, ?)',(task_id,row[1],row[2],row[3],row[4],row[5],stamp));self.renumber(db)
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
		if not isinstance(data,(bytes,bytearray)):raise TypeError('An upload is bytes')
		if not data:raise ValueError('An upload must not be empty')
		if len(data)>MAX_UPLOAD:raise ValueError(f"An upload must be {MAX_UPLOAD:,} bytes or fewer")
		cleaned=upload_name(name);kind=upload_type(content_type);digest=hashlib.sha256(data).hexdigest();upload_id=identifier(upload_id)if upload_id is not None else new_id();note_id=identifier(note_id)if note_id is not None else upload_id
		if position is not None and(not isinstance(position,int)or not 1<=position<=MAX_ATTACHMENTS):raise ValueError('Invalid attachment position')
		directory=self.path.parent/UPLOAD_DIR;directory.mkdir(parents=True,exist_ok=True,mode=448);stem=f"{note_id}-{position}"if position is not None else upload_id;target=directory/f"{stem}{Path(cleaned).suffix[:16]}"
		with self.transaction(shared)as db:
			if shared is None:db.execute('BEGIN IMMEDIATE')
			existing=db.execute('SELECT * FROM uploads WHERE id = ?',(upload_id,)).fetchone()
			if existing:
				if(existing['note_id'],existing['name'],existing['type'],existing['size'],existing['sha256'],existing['file'])!=(note_id,cleaned,kind,len(data),digest,target.name):raise ValueError('This note ID already belongs to a different file')
				target=directory/existing['file']
				if not target.is_file()or hashlib.sha256(target.read_bytes()).hexdigest()!=digest:target.write_bytes(bytes(data));target.chmod(384)
				return upload_row(existing,self.path.parent)
			target.write_bytes(bytes(data));target.chmod(384);db.execute('INSERT INTO uploads (id, note_id, name, type, size, sha256, file, at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)',(upload_id,note_id,cleaned,kind,len(data),digest,target.name,now()));row=db.execute('SELECT * FROM uploads WHERE id = ?',(upload_id,)).fetchone()
		return upload_row(row,self.path.parent)
	def note_with_uploads(self,note_id,text,files):
		identifier(note_id);note_text(text)
		if not isinstance(files,(list,tuple))or not 1<=len(files)<=MAX_ATTACHMENTS:raise ValueError(f"Attach 1–{MAX_ATTACHMENTS} files to a note")
		prepared=[]
		for file in files:
			if not isinstance(file,(list,tuple))or len(file)!=3:raise ValueError('Each attachment needs a name, type and bytes')
			name,content_type,data=file
			if not isinstance(data,(bytes,bytearray)):raise TypeError('An upload is bytes')
			if not data or len(data)>MAX_UPLOAD:raise ValueError(f"Each attachment must be 1–{MAX_UPLOAD:,} bytes")
			prepared.append((upload_name(name),upload_type(content_type),bytes(data)))
		created=[]
		try:
			with self.transaction()as db:
				db.execute('BEGIN IMMEDIATE');existing=db.execute('SELECT text FROM notes WHERE id = ?',(note_id,)).fetchone();rows=db.execute('SELECT * FROM uploads WHERE note_id = ? ORDER BY seq',(note_id,)).fetchall()
				if existing and existing['text']!=text:raise ValueError('This message ID already belongs to different text')
				if rows:
					if len(rows)!=len(prepared)or any((row['name'],row['type'],row['size'],row['sha256'])!=(name,kind,len(data),hashlib.sha256(data).hexdigest())for(row,(name,kind,data))in zip(rows,prepared)):raise ValueError('This note ID already belongs to different files')
					for(row,(_,_,data))in zip(rows,prepared):
						target=self.path.parent/UPLOAD_DIR/row['file']
						if not target.is_file()or hashlib.sha256(target.read_bytes()).hexdigest()!=row['sha256']:target.parent.mkdir(parents=True,exist_ok=True,mode=448);target.write_bytes(data);target.chmod(384)
					records=[upload_row(row,self.path.parent)for row in rows]
				else:
					if existing:raise ValueError('This note ID was already sent without a file')
					records=[]
					for(index,(name,kind,data))in enumerate(prepared,1):record=self.save_upload(name,kind,data,upload_id=note_id if index==1 else new_id(),note_id=note_id,position=index if len(prepared)>1 else None,shared=db);records.append(record);created.append(Path(record['path']))
				note=self.note(note_id,text,shared=db);return add_note_attachments(note,records)
		except Exception:
			for path in created:path.unlink(missing_ok=True)
			raise
	def note_with_upload(self,note_id,text,name,content_type,data):return self.note_with_uploads(note_id,text,[(name,content_type,data)])
	def fetch_jobs(self):
		with closing(self.connect())as db:rows=db.execute('SELECT * FROM fetch_jobs ORDER BY seq DESC').fetchall()
		return[fetch_row(row,self.path.parent)for row in rows]
	def enqueue_fetch(self,url,allow_proxy,*,pending=False):
		url=fetch_url(url)
		if not isinstance(allow_proxy,bool):raise TypeError('Proxy fallback must be true or false for this URL')
		if not isinstance(pending,bool):raise TypeError('Pending approval must be true or false')
		job_id,stamp=new_id(),now()
		with self.transaction(autosave=False)as db:db.execute("INSERT INTO fetch_jobs (id, url, allow_proxy, status, approval, at, updated_at) VALUES (?, ?, ?, 'queued', ?, ?, ?)",(job_id,url,int(allow_proxy),'pending'if pending else'approved',stamp,stamp));row=db.execute('SELECT * FROM fetch_jobs WHERE id = ?',(job_id,)).fetchone()
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
		if not data or len(data)>MAX_FETCH:raise ValueError(f"A download must be 1–{MAX_FETCH:,} bytes")
		if source not in{'direct','allorigins','codetabs'}:raise ValueError('Unknown download source')
		cleaned=upload_name(name);file_type=upload_type(content_type);target=None
		try:
			with self.transaction(autosave=False)as db:
				row=self.claimed_fetch(db,job_id,claim)
				if source!='direct'and not row['allow_proxy']:raise ValueError('Proxy fallback was not enabled for this URL')
				directory=self.path.parent/FETCH_DIR;directory.mkdir(parents=True,exist_ok=True,mode=448);target=directory/f"{job_id}{Path(cleaned).suffix[:16]}";target.write_bytes(data);target.chmod(384);stamp=now();db.execute("UPDATE fetch_jobs SET status = 'saved', source = ?, name = ?, type = ?, size = ?, sha256 = ?, file = ?, error = NULL, claim = NULL, lease_until = NULL, updated_at = ? WHERE id = ?",(source,cleaned,file_type,len(data),hashlib.sha256(data).hexdigest(),target.name,stamp,job_id));message=f"Download: {cleaned} ({len(data)} B, {file_type}) from {urlsplit(row['url']).hostname} via {source} saved to {target}";db.execute('INSERT INTO notes (id, text, at) VALUES (?, ?, ?)',(job_id,message,stamp));result=db.execute('SELECT * FROM fetch_jobs WHERE id = ?',(job_id,)).fetchone()
		except Exception:
			if target is not None:target.unlink(missing_ok=True)
			raise
		self.autosave();return fetch_row(result,self.path.parent)
	def import_tasks(self,records,replace=False,autosave=True):
		if not isinstance(records,list):raise TypeError('Import a list of task objects')
		prepared=[]
		for(index,record)in enumerate(records,1):
			if not isinstance(record,dict):raise TypeError('Import a list of task objects')
			details=[str(item)for item in record.get('details')or[]if str(item).strip()];status=record.get('status');check_task(record.get('id'),record.get('title'),details)
			if status is not None and status not in TASK_STATUSES:raise ValueError(f"A task is either {' or '.join(TASK_STATUSES)}")
			prepared.append((record.get('id'),record.get('title'),details,status,record.get('order')or index))
		with self.transaction(autosave=autosave)as db:
			if replace:
				for(task_id,title,_,_,_)in prepared:
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
	def set_meta(self,key,value):
		with closing(self.connect())as db,db:db.execute('INSERT OR REPLACE INTO meta VALUES (?, ?)',(key,str(value)))
	def reminder(self,advance=False):
		with closing(self.connect())as db,db:
			uploads=db.execute('SELECT count(*) FROM notes JOIN uploads USING (id) WHERE acknowledged_at IS NULL').fetchone()[0];notes=db.execute('SELECT count(*) FROM notes WHERE acknowledged_at IS NULL').fetchone()[0]-uploads;reports=db.execute('SELECT count(*) FROM submissions WHERE acknowledged_at IS NULL').fetchone()[0];cursor=meta_number(db,REMINDER_CURSOR);polls=0 if advance and not notes+reports+uploads else meta_number(db,POLLS_SINCE_MESSAGE)+(1 if advance else 0);db.execute('INSERT OR REPLACE INTO meta VALUES (?, ?)',(REMINDER_CURSOR,str(cursor+1)))
			if advance:db.execute('INSERT OR REPLACE INTO meta VALUES (?, ?)',(POLLS_SINCE_MESSAGE,str(polls)))
		counts=[f"{count} {kind}/s."for(count,kind)in((notes,'message'),(reports,'form answer'),(uploads,'upload'))if count];ack=['DO NOT IGNORE. ACK ASAP.']if counts else[];head=[f"{polls} call/s since user messaged."]if polls and counts else[];return' '.join([*head,*counts,*ack,REMINDERS[cursor%len(REMINDERS)]])
	def read(self):
		with self.transaction()as db:
			pending=[dict(row)|{'kind':'note'}for row in db.execute('SELECT * FROM notes WHERE acknowledged_at IS NULL ORDER BY seq')];pending+=[dict(row)|{'kind':'report'}for row in db.execute('SELECT * FROM submissions WHERE acknowledged_at IS NULL ORDER BY seq')];attachments={}
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
		text=data.decode('utf-8');parse_fields(text)
		with self.transaction()as db:
			answered=db.execute('SELECT count(*) FROM submissions WHERE report_id = ?',(report_id,)).fetchone()[0]
			if answered:raise ValueError(f"Report {report_id} has submitted answers; publish the update under a new ID")
			highest=db.execute('SELECT COALESCE(MAX(seq), 0) FROM reports').fetchone()[0];db.execute('INSERT INTO reports (id, title, markdown, updated_at, seq)\n           VALUES (?, ?, ?, ?, ?)\n           ON CONFLICT(id) DO UPDATE SET title = excluded.title,\n             markdown = excluded.markdown, updated_at = excluded.updated_at,\n             seq = COALESCE(reports.seq, excluded.seq), seen_at = NULL',(report_id,title,text,now(),highest+1))
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
			db.execute('UPDATE reports SET seen_at = COALESCE(seen_at, ?) WHERE id = ?',(now(),report_id));return dict(db.execute('SELECT * FROM reports WHERE id = ?',(report_id,)).fetchone())
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
				elif not isinstance(value,str)or len(value)>2000:raise ValueError(f"{field_id}: text answers are 1–2000 characters")
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
def render(markdown,breaks=False):
	try:from markdown_it import MarkdownIt
	except ImportError as error:raise RuntimeError("Markdown rendering needs markdown-it-py. Install it in the preview's venv and restart the server with that venv's Python; steering still works.")from error
	parser=MarkdownIt('commonmark',{'html':False,'breaks':breaks}).enable(['table','strikethrough']);parser.add_render_rule('link_open',open_link);rendered=drop_paragraph_breaks(parser.render(unescape_fences(markdown)));return add_copy_buttons(rendered)
def handler(store):
	token=secrets.token_urlsafe(32)
	class Handler(BaseHTTPRequestHandler):
		def setup(self):super().setup();self.connection.settimeout(15)
		def reply(self,status,body,content_type='application/json; charset=utf-8',filename=None):
			data=body if isinstance(body,(bytes,bytearray))else body.encode('utf-8');self.send_response(status);self.send_header('Content-Type',content_type);self.send_header('Content-Length',str(len(data)));self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff');self.send_header('Content-Security-Policy',"default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self' https:; base-uri 'none'; form-action 'self'")
			if filename:self.send_header('Content-Disposition',f'attachment; filename="{filename}"')
			self.end_headers();self.wfile.write(data)
		def problem(self,status,error):self.reply(status,json.dumps({'error':str(error)}))
		def do_GET(self):
			path=urlsplit(self.path).path
			try:
				if path=='/':page=(ASSETS/'index.html').read_text(encoding='utf-8');page=page.replace('__STYLE__',(ASSETS/'style.css').read_text(encoding='utf-8'));page=page.replace('__SCRIPT__',(ASSETS/'app.js').read_text(encoding='utf-8'));self.reply(200,page.replace('__TOKEN__',token),'text/html; charset=utf-8');return
				if path=='/api/state':
					state=store.state();state['token']=token
					try:
						for item in state['notes']:
							item['html']=render(item['text'])
							if item.get('ack_kind')=='reply'and item.get('ack_text'):item['ack_html']=render(item['ack_text'])
							for reply in item.get('replies')or[]:
								if reply['kind']=='reply':reply['html']=render(reply['text'])
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
						self.reply(200,json.dumps({'html':body,'fields':len(questions),'revision':report['updated_at']},ensure_ascii=False));return
					self.reply(200,report['markdown'],'text/plain; charset=utf-8',f"{report_id}.md");return
				self.problem(404,'Not found')
			except FileNotFoundError as error:self.problem(404,error)
			except(OSError,sqlite3.Error,RuntimeError)as error:self.problem(503,error)
		def do_POST(self):
			path=urlsplit(self.path).path;report_submit=re.fullmatch('/api/reports/([a-zA-Z0-9_-]{1,80})/submit',path);report_seen=re.fullmatch('/api/reports/([a-zA-Z0-9_-]{1,80})/seen',path);report_unpublish=re.fullmatch('/api/reports/([a-zA-Z0-9_-]{1,80})/unpublish',path);fetch_post=re.fullmatch('/api/fetch-jobs/([a-zA-Z0-9_-]{1,80})/(renew|result|fail|retry|approve|deny)',path);upload_post=path=='/api/uploads';note_upload=path=='/api/notes/with-file';fetch_result=bool(fetch_post and fetch_post.group(2)=='result')
			if path not in{'/api/notes','/api/markdown','/api/fetch-jobs','/api/fetch-jobs/claim'}and not report_submit and not report_seen and not report_unpublish and not upload_post and not note_upload and not fetch_post:self.problem(404,'Not found');return
			content_type=self.headers.get('Content-Type','')
			if note_upload:
				if not content_type.lower().startswith('multipart/form-data;'):self.problem(415,'Expected multipart/form-data');return
			elif content_type!='application/json'and not(upload_post or fetch_result):self.problem(415,'Expected application/json');return
			try:
				length=int(self.headers.get('Content-Length','0'));limit=MAX_ATTACHMENTS*MAX_UPLOAD+MAX_BODY if note_upload else MAX_UPLOAD if upload_post else MAX_FETCH if fetch_result else MAX_SUBMISSION_BODY if report_submit else MAX_BODY
				if not 0<length<=limit:
					subject='Upload'if upload_post or note_upload else'Download'if fetch_result else'Request body';remaining=length if 0<length<=limit+1 else 0
					while remaining>0:
						chunk=self.rfile.read(min(65536,remaining))
						if not chunk:break
						remaining-=len(chunk)
					self.problem(413,f"{subject} must be 1–{limit:,} bytes");return
				data=self.rfile.read(length)
				if len(data)!=length:self.problem(400,'Incomplete request body; retry the upload or request');return
				if note_upload:
					note=store.note_with_uploads(*parse_note_attachments(content_type,data))
					for key in('at','acknowledged_at','ack_edited_at','seen_at'):note[key]=clip_stamp(note[key])
					self.reply(201,json.dumps(note,ensure_ascii=False));return
				if upload_post:name=parse_qs(urlsplit(self.path).query).get('name',[''])[0];record=store.save_upload(name,self.headers.get('Content-Type',''),data);record['at']=clip_stamp(record['at']);store.note(record['id'],f"Upload: {record['name']} ({record['size']} B, {record['type']or'unknown type'}) saved to {record['path']}");self.reply(201,json.dumps(record,ensure_ascii=False));return
				if fetch_result:
					name=parse_qs(urlsplit(self.path).query).get('name',[''])[0];record=store.complete_fetch(fetch_post.group(1),self.headers.get('X-Fetch-Claim',''),name,self.headers.get('Content-Type',''),self.headers.get('X-Fetch-Source',''),data)
					for key in('at','updated_at'):record[key]=clip_stamp(record[key])
					self.reply(201,json.dumps(record,ensure_ascii=False));return
				payload=json.loads(data)
				if not isinstance(payload,dict):self.problem(400,'Expected a JSON object');return
				if path=='/api/fetch-jobs':record=store.enqueue_fetch(payload.get('url'),payload.get('allow_proxy',False));self.reply(201,json.dumps(record,ensure_ascii=False));return
				if path=='/api/fetch-jobs/claim':self.reply(200,json.dumps({'job':store.claim_fetch()},ensure_ascii=False));return
				if fetch_post:
					job_id,action=fetch_post.groups()
					if action=='renew':record=store.renew_fetch(job_id,self.headers.get('X-Fetch-Claim',''))
					elif action=='fail':record=store.fail_fetch(job_id,self.headers.get('X-Fetch-Claim',''),payload.get('error',''))
					elif action in{'approve','deny'}:record=store.decide_fetch(job_id,'approved'if action=='approve'else'denied')
					else:record=store.retry_fetch(job_id)
					self.reply(200,json.dumps(record,ensure_ascii=False));return
				if path=='/api/markdown':self.reply(200,render(note_text(payload.get('text')),breaks=True),'text/html; charset=utf-8');return
				if report_seen:
					report=store.mark_report_seen(report_seen.group(1))
					for key in('updated_at','seen_at'):report[key]=clip_stamp(report[key])
					self.reply(200,json.dumps(report,ensure_ascii=False));return
				if report_unpublish:store.unpublish(report_unpublish.group(1));self.reply(200,json.dumps({'unpublished':report_unpublish.group(1)}));return
				if report_submit:
					note=store.submit_report(report_submit.group(1),payload.get('id'),payload.get('answers'),payload.get('revision'))
					for key in('at','acknowledged_at','ack_edited_at','seen_at'):note[key]=clip_stamp(note[key])
					self.reply(201,json.dumps(note,ensure_ascii=False));return
				note=store.note(payload.get('id'),payload.get('text'))
				for key in('at','acknowledged_at','ack_edited_at','seen_at'):note[key]=clip_stamp(note[key])
				self.reply(201,json.dumps(note,ensure_ascii=False))
			except(ReportChanged,FetchChanged)as error:self.problem(409,error)
			except FileNotFoundError as error:self.problem(404,error)
			except(ValueError,TypeError,UnicodeDecodeError)as error:self.problem(400,error)
			except(OSError,sqlite3.Error,RuntimeError)as error:self.problem(503,error)
	return Handler
def main():
	parser=argparse.ArgumentParser(description=CLI_DESCRIPTION);parser.add_argument('--state-dir',default='arena-state');parser.add_argument('--reminder',action='store_true',help='Print the unacked-count reminder line and exit');parser.add_argument('--save-path',default=None,help='Where the save button writes its file; inside the state directory by default');parser.add_argument('--pretty',action='store_true',help='Indent the JSON this CLI prints; agent-facing output is minified by default');commands=parser.add_subparsers(dest='command',required=False);serve=commands.add_parser('serve');serve.add_argument('--port',type=int,default=8000,help='Port to bind (default: 8000)');commands.add_parser('init');commands.add_parser('read');download=commands.add_parser('download-request',help='Request an HTTPS browser download, pending a preview Approve click');download.add_argument('url',help='One HTTPS URL without embedded credentials');download.add_argument('--allow-proxy',action='store_true',help='Let the owner opt in to AllOrigins and CodeTabs fallback for this request');seen=commands.add_parser('seen');seen.add_argument('ids',nargs='+');ack=commands.add_parser('ack');ack.add_argument('ids',nargs='+');ack.add_argument('--reply',help='Markdown answer shown in the message log');ack.add_argument('--note',help='Short plain answer shown in the message log');publish=commands.add_parser('publish');publish.add_argument('source',type=Path);publish.add_argument('--id',required=True);publish.add_argument('--title',required=True);unpublish=commands.add_parser('unpublish');unpublish.add_argument('report_id');task=commands.add_parser('task');task.add_argument('id_arg',nargs='?',metavar='TASK-ID');task.add_argument('title_arg',nargs='?',metavar='TASK-TITLE');task.add_argument('detail_arg',nargs='*',metavar='TASK-DETAIL');task.add_argument('--task-id',help='The ID the first positional takes');task.add_argument('--task-title',help='The title the second positional takes');task.add_argument('--task-details',action='append',help='One detail line, repeatable; an empty string clears the list');task.add_argument('--msg-id',help='Message this task answers; marks that message as having a task');task.add_argument('--amend',metavar='PREV-ID',help='Rename the task stored under this ID to the one given');task.add_argument('--status',choices=TASK_STATUSES,default=None);task.add_argument('--order',type=int,default=None,help='1-based place in its div, not the end');task_remove=commands.add_parser('task-remove');task_remove.add_argument('task_id');commands.add_parser('task-list');task_import=commands.add_parser('task-import');task_import.add_argument('source',nargs='?',type=Path,help='JSON array or one task per line; stdin if omitted');task_import.add_argument('--replace',action='store_true',help='Clear the stored list before importing');legacy=commands.add_parser('import-notes');legacy.add_argument('source',type=Path);args=parser.parse_args()
	try:
		if args.reminder:store=Store(args.state_dir,create=False,save_path=args.save_path);require_server(store);print(store.reminder(advance=True),flush=True);return 0
		if not args.command:parser.error('a command is required')
		store=Store(args.state_dir,create=args.command in{'serve','init'},save_path=args.save_path);print(store.reminder(),file=sys.stderr,flush=True)
		if args.command=='serve':
			require_renderer()
			with ThreadingHTTPServer(('0.0.0.0',args.port),handler(store))as server:store.set_meta('port',str(server.server_port));print(f"Preview listening on 0.0.0.0:{server.server_port}; state: {store.path}",flush=True);server.serve_forever()
		elif args.command=='read':require_server(store);print_read(store,args.pretty)
		elif args.command=='download-request':print(cli_json(store.enqueue_fetch(args.url,args.allow_proxy,pending=True),args.pretty))
		elif args.command=='seen':store.mark_seen(args.ids);print('Seen: '+', '.join(args.ids))
		elif args.command=='ack':
			if bool(args.reply)==bool(args.note):raise ValueError('Choose exactly one of --reply or --note')
			kind='reply'if args.reply else'note';store.acknowledge(args.ids,kind,args.reply or args.note);print('Acknowledged: '+', '.join(args.ids));print('If a note asks for work, add it to the task list: '+'; '.join(f'task <id> "<title>" --msg-id {i}'for i in args.ids))
		elif args.command=='publish':store.publish(args.id,args.title,args.source);print(f"Published {args.id}; select it in the Reports tab")
		elif args.command=='unpublish':store.unpublish(args.report_id);print(f"Unpublished {args.report_id}; its answers and source file remain")
		elif args.command=='task':
			task_id=args.task_id or args.id_arg
			if not task_id:raise ValueError('A task needs an ID')
			details=args.task_details
			if details is None and args.detail_arg:details=args.detail_arg
			if args.amend:store.amend_task(args.amend,task_id)
			if args.msg_id:
				with store.transaction()as shared:record=store.write_task(task_id,args.task_title or args.title_arg,details,args.status,args.order,shared=shared);store.mark_task(args.msg_id,task_id,shared=shared)
			else:record=store.write_task(task_id,args.task_title or args.title_arg,details,args.status,args.order)
			before,after=store.neighbours(task_id);echo=echo_task(record,before,after)
			if args.msg_id:echo['msg_id']=args.msg_id
			print(cli_json(echo,args.pretty))
		elif args.command=='task-remove':print(cli_json(echo_task(store.remove_task(args.task_id)),args.pretty))
		elif args.command=='task-list':print(cli_json(store.list_tasks(),args.pretty))
		elif args.command=='task-import':text=args.source.read_text(encoding='utf-8')if args.source else sys.stdin.read();written=store.import_tasks([record for record in parse_task_import(text)if not(isinstance(record,dict)and'text'in record and'title'not in record)],args.replace,autosave=False);print(cli_json({'imported':len(written),'replaced':args.replace,'ids':[item['id']for item in written]},args.pretty))
		elif args.command=='import-notes':
			saved=[json.loads(line)for line in args.source.read_text(encoding='utf-8').splitlines()if line.strip()];answers=[record for record in saved if isinstance(record,dict)and'report_id'in record];records=[record for record in saved if not(isinstance(record,dict)and('title'in record and'text'not in record or'report_id'in record))]
			for record in answers:store.submission(record['id'],record['report_id'],record['text'],record.get('at'),acknowledged_at=record.get('acknowledged_at'),ack_kind=record.get('ack_kind'),ack_text=record.get('ack_text'),ack_edited_at=record.get('ack_edited_at'),seen_at=record.get('seen_at'),task_id=record.get('task_id'),autosave=False,replies=record.get('replies'))
			for record in records:store.note(record['id'],record['text'],record.get('at'),acknowledged_at=record.get('acknowledged_at'),ack_kind=record.get('ack_kind'),ack_text=record.get('ack_text'),ack_edited_at=record.get('ack_edited_at'),seen_at=record.get('seen_at'),task_id=record.get('task_id'),autosave=False,replies=record.get('replies'))
			receipts=sum(1 for record in records if record.get('acknowledged_at'));print(f"Imported {len(records)} notes, {receipts} with a receipt restored verbatim, {len(answers)} report answers; existing IDs are not duplicated and keep the receipt they have")
	except(OSError,ValueError,TypeError,KeyError,sqlite3.Error,RuntimeError)as error:print(f"Preview error: {error}",file=sys.stderr);return 1
	return 0
if __name__=='__main__':raise SystemExit(main())
