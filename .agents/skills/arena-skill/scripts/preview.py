import argparse
import base64
import hashlib
import html
import json
import os
import re
import secrets
import shlex
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from contextlib import closing,contextmanager,nullcontext
from datetime import UTC,datetime,timedelta
from fnmatch import fnmatchcase
from pathlib import Path
from urllib.parse import parse_qs,quote,unquote,urlsplit,urlunsplit
from zoneinfo import ZoneInfo
HAS_RENDERER=None
sqlite3=None
def __getattr__(name):
	if name in('BaseHTTPRequestHandler','ThreadingHTTPServer'):from http import server;return getattr(server,name)
	raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
ASSETS=Path(__file__).resolve().parents[1]/'assets'
IDENTIFIER=re.compile('[a-zA-Z0-9_-]{1,80}\\Z')
VIOLATION_NOTES="type NOT IN ('', 'survey', 'state-backup', 'worker-idle', 'worker-disconnected')"
PENDING_SUBMISSIONS='acknowledged_at IS NULL AND seq IN (SELECT MAX(seq) FROM submissions GROUP BY report_id)'
REPLY_REFERENCE=re.compile('^RE:\\s*([a-zA-Z0-9_-]{1,80}(?:/[a-zA-Z0-9_-]{1,80})?)(?:\\s|$)',re.IGNORECASE)
MAX_REPORT=2000000
MAX_FIELDS=24
WS_TICK_SECONDS=.25
WS_HEARTBEAT_SECONDS=10
WS_IDLE_SECONDS=35
WS_MAX_MESSAGE=MAX_REPORT+4096
SNAPSHOT_CAP_BYTES=128000000
IMAGE_DATA_URI_MAX_BYTES=2*1024*1024
WORKSPACE_SKIP={'.arena','.cache','.git','.local','.mypy_cache','.next','.nox','.npm','.nuxt','.output','.parcel-cache','.pytest_cache','.ruff_cache','.svelte-kit','.tox','.turbo','.venv','.vite','__pycache__','build','coverage','dist','node_modules','out','target'}
FETCH_LEASE=timedelta(minutes=5)
UPLOAD_DIR='uploads'
FETCH_DIR='downloads'
def read_set_files(root=None,worker=False):
	base=Path(root or Path.cwd()).resolve();cache_key=base,bool(worker);cached=READ_SET_CACHE.get(cache_key)
	if cached is not None:return list(cached)
	files=[]
	if(base/'ARENA.md').is_file():files.append('ARENA.md')
	for(dirpath,dirnames,filenames)in os.walk(base,followlinks=False):
		dirnames[:]=sorted(name for name in dirnames if name not in WORKSPACE_SKIP)
		if'AGENTS.md'in filenames:files.append((Path(dirpath)/'AGENTS.md').relative_to(base).as_posix())
	for name in('.agents/skills/arena-skill/SKILL.md','skills/arena-skill/SKILL.md'):
		if(base/name).is_file():files.append(name);break
	if worker:
		for name in('.agents/skills/arena-skill/WORKER.md','skills/arena-skill/WORKER.md'):
			if(base/name).is_file():files.append(name);break
	READ_SET_CACHE[cache_key]=tuple(files);return list(files)
def state_dir_usage(state_dir=None):
	root=Path(state_dir if state_dir is not None else resolve_state_dir());categories={'database':{'bytes':0,'files':0},'backups':{'bytes':0,'files':0},'uploads':{'bytes':0,'files':0},'downloads':{'bytes':0,'files':0},'runs':{'bytes':0,'files':0},'reports':{'bytes':0,'files':0},'other':{'bytes':0,'files':0}};total_bytes=0;total_files=0
	if not root.is_dir():return{'bytes':0,'files':0,'categories':categories}
	for(dirpath,_,filenames)in os.walk(root,followlinks=False):
		rel_parts=Path(dirpath).relative_to(root).parts;top=rel_parts[0]if rel_parts else''
		for name in filenames:
			target=Path(dirpath)/name
			try:size=target.stat().st_size
			except OSError:continue
			total_bytes+=size;total_files+=1
			if not top and(name.startswith('state.sqlite3')or name=='display_tz'):bucket='database'
			elif top in{'backups','uploads','downloads','runs'}:bucket=top
			elif not top and name.endswith('.md'):bucket='reports'
			else:bucket='other'
			categories[bucket]['bytes']+=size;categories[bucket]['files']+=1
	return{'bytes':total_bytes,'files':total_files,'categories':categories}
def workspace_usage(root=None,state_dir=None):
	root=Path(root or Path.cwd());total=0;count=0
	for(dirpath,dirnames,filenames)in os.walk(root,followlinks=False):
		dirnames[:]=[name for name in dirnames if name not in WORKSPACE_SKIP]
		for name in filenames:
			try:total+=(Path(dirpath)/name).stat().st_size
			except OSError:continue
			count+=1
	target_state=Path(state_dir)if state_dir is not None else root/'arena-state';return{'bytes':total,'files':count,'documented_cap_bytes':SNAPSHOT_CAP_BYTES,'download_cap_bytes':None,'arena_state':state_dir_usage(target_state)}
FENCE=re.compile('^ {0,3}(`{3,}|~{3,})')
CHOICE=re.compile('^\\s*[-*]\\s+\\(([ xX]?)\\)\\s+(\\S.*?)\\s*$')
CHECKBOX=re.compile('^\\s*[-*]\\s+\\[([ xX]?)\\]\\s+(\\S.*?)\\s*$')
BLANK=re.compile('^(?:(.*?)[\\s:])?_{3,}\\s*$')
ANCHOR=re.compile('\\s*\\{#([a-zA-Z0-9_-]{1,80})\\}\\s*$')
COMMENT_ANCHOR=re.compile('<!--\\s*(?:id\\s*[:=]\\s*|#)([a-zA-Z0-9_-]{1,80})\\s*-->',re.IGNORECASE)
BLOCK_START=re.compile('^ {0,3}(?:#{1,6}(?:\\s|$)|>|[-*+](?:\\s|$)|\\d+[.)](?:\\s|$)|(?:[-*_]\\s*){3,}$|`{3,}|~{3,})')
TASK_REMINDER='{remaining} tasks left.'
def fill_reminder(tail,remaining):
	if'{remaining}'in tail and remaining==0:return None
	count=f"{remaining} task"if remaining==1 else f"{remaining} tasks";return tail.replace('{remaining} tasks',count)
PRIVATE_BATCH_CAP=3
REPO_PRIVATE_META='repo_private'
def unpushed_reminder_text(unpushed,private=False):
	if not unpushed:return''
	prefix=f"{unpushed} commit/s unpushed."
	if private:
		if unpushed>=PRIVATE_BATCH_CAP:return f"{prefix} Batch cap ({PRIVATE_BATCH_CAP}) reached, so push now."
		return f"{prefix} Private repo: push small batches (<={PRIVATE_BATCH_CAP}), and never starve pushes."
	return f"{prefix} ALWAYS push. NEVER delay for batch."
def reminder_tail(cursor,remaining,stale=0,unpushed=0,private=False):
	for step in range(len(REMINDERS)):
		tail=fill_reminder(REMINDERS[(cursor+step)%len(REMINDERS)],remaining)
		if tail:
			if tail=='Unpublish stale reports.'and stale:return f"Unpublish {stale} stale report{'s'if stale!=1 else''}."
			if tail=='Always push.'and unpushed:return unpushed_reminder_text(unpushed,private=private)
			return tail
	return REMINDERS[cursor%len(REMINDERS)]
REMINDER_CHAR_CAP=88
REMINDERS='If bible absent, read ARENA.md, AGENTS.md, and SKILL.md in full first.','`task list` first; update as work changes.','Smallest open task next.','Always push.','`ask_user` on GH_TOKEN failure.','Docs: terse, clear.','Ask questions in reports.','Publish reports.','Never end turn with unblocked tasks.','Unpublish stale reports.','End turn with `poll`.','Grep-verify each edit.',TASK_REMINDER,'Before push: rebase `origin/main`; empty checks mean it moved.','Before pushed-turn end: check PR CI; read verdict on a later call, never loop.','Run `arena-preview`, not full path.','NEVER spam `run logs`.','`bash` starts at repo root; `cd` into it is redundant.'
REMINDER_CURSOR='reminder_cursor'
POLLS_SINCE_MESSAGE='polls_since_message'
GATE_THRESHOLD=6
STALE_REPORT_THRESHOLD=10
DEFAULT_PORT=8000
AGENT_KEY_META='agent_key'
USERSCRIPT_VERSION_HEADER='X-Clankers-Userscript-Version'
USERSCRIPT_VERSION_META='userscript_version'
EGRESS_VERSION_META='egress_version'
USERSCRIPT_VERSION_RE=re.compile('^[0-9A-Za-z._+-]{1,32}$')
AGENT_KEY_EXPIRY_SECONDS=1200
AGENT_KEY_TTL_SECONDS=900
AGENT_KEY_EXPIRY_META='agent_key_expired_at'
AGENT_SEEN_META='agent_seen_at'
TOOL_NAME_META='last_tool_name'
TOOL_AT_META='last_tool_at'
TOOL_PROBE_NAMES='read','edit','write'
AGENT_INTERRUPT_META='agent_interrupt'
AGENT_INTERRUPT_FLAG_META='agent_interrupt_flag'
VIOLATION_TYPES={'notes':('bash-violation','process-violation','missing-details','out-of-scope','task-not-created'),'downloads':('bash-violation','process-violation'),'reports':('format-violation','unnecessary-report','wall-of-text'),'tasks':('current-task-violation','empty-task-list','missing-details','out-of-scope','task-not-created')}
READ_SET_CACHE={}
CALL_GROUP_META='call_group'
CALL_STARTED_META='call_started_at'
AGENT_CALL_ENDED_META='agent_call_ended_at'
CALL_STOP_TYPE='bash-violation'
CALL_STOP_SECONDS=3
TURN_ENDED_META='turn_ended_at'
REMOVED_META='state_removed_at'
REPORT_CHANGE_VIEW_SECONDS=60
REPORT_IDLE_SECONDS=30*60
AGENT_KEY_RE=re.compile('[A-Za-z0-9_-]{20,64}\\Z')
AGENT_HOST_RE=re.compile('https://[A-Za-z0-9.-]+\\Z')
def now():return datetime.now(UTC).isoformat()
TIMEZONE_META='display_tz'
DISPLAY_ZONE=None
MONTH_ABBREVIATIONS='Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'
def zone_exists(name):
	try:ZoneInfo(name);return True
	except(KeyError,ValueError,OSError):return False
def resolve_display_zone(store):
	name=os.environ.get('TZ')or(store.meta_value(TIMEZONE_META)if store else None)
	if not name or not zone_exists(name):return None
	return ZoneInfo(name)
def local_stamp(value,moment=None):
	if not value:return value
	if DISPLAY_ZONE is None:return clip_stamp(value)
	try:parsed=datetime.fromisoformat(value)
	except ValueError:return clip_stamp(value)
	if parsed.tzinfo is None:parsed=parsed.replace(tzinfo=UTC)
	local=parsed.astimezone(DISPLAY_ZONE);current=moment or datetime.now(DISPLAY_ZONE)
	if current.tzinfo is None:current=current.replace(tzinfo=UTC)
	current=current.astimezone(DISPLAY_ZONE);time_text=f"{local.hour:02d}:{local.minute:02d}"
	if local.date()==current.date():return time_text
	return f"{MONTH_ABBREVIATIONS[local.month-1]} {local.day:02d} {time_text}"
def workspace_branch(root=None):
	try:head=(Path(root or Path.cwd())/'.git'/'HEAD').read_text(encoding='utf-8').strip()
	except OSError:return''
	prefix='ref: refs/heads/';return head[len(prefix):]if head.startswith(prefix)else''
GIT_SHA_RE=re.compile('^[0-9a-f]{40}$')
def workspace_git_ref(root=None,ref='',depth=0):
	if depth>4 or not ref or not str(ref).startswith('refs/'):return''
	git_dir=Path(root or Path.cwd())/'.git'
	try:
		if git_dir.is_file():
			pointer=git_dir.read_text(encoding='utf-8').strip()
			if pointer.startswith('gitdir:'):git_dir=(git_dir.parent/pointer.split(':',1)[1].strip()).resolve()
		loose=git_dir/ref
		if loose.is_file():
			val=loose.read_text(encoding='utf-8').strip()
			if val.startswith('ref: '):return workspace_git_ref(root,val[5:].strip(),depth+1)
			low=val.lower();return low if GIT_SHA_RE.fullmatch(low)else''
		packed=git_dir/'packed-refs'
		if packed.is_file():
			for raw_line in packed.read_text(encoding='utf-8',errors='ignore').splitlines():
				line=raw_line.strip()
				if not line or line.startswith(('#','^')):continue
				parts=line.split(' ',1)
				if len(parts)==2 and parts[1].strip()==ref:low=parts[0].strip().lower();return low if GIT_SHA_RE.fullmatch(low)else''
	except OSError:return''
	return''
def clear_skip_poll(db):db.execute('DELETE FROM meta WHERE key = ?',(SKIP_POLL_META,))
def reset_poll_count(db):
	if gate_pending_count(db)==1:db.execute("INSERT OR REPLACE INTO meta VALUES (?, '0')",(POLLS_SINCE_MESSAGE,))
def meta_number(db,key):row=db.execute('SELECT value FROM meta WHERE key = ?',(key,)).fetchone();value=str(row[0])if row else'';return int(value)if value.isdigit()else 0
def pending_board_entries_for_db(db,branch=''):
	row=db.execute('SELECT value FROM meta WHERE key = ?',(BOARD_STATE_META,)).fetchone()
	if not row or not row[0]:return[]
	try:raw=json.loads(str(row[0]))
	except Exception:return[]
	if not isinstance(raw,dict):return[]
	br=board_branch(branch or raw.get('local_branch')or raw.get('local_session_id')or workspace_branch())
	if not br:return[]
	norm=normalize_board_state(raw,branch=br)
	if br not in set(norm.get('allowed_sessions')or[]):return[]
	return[entry for entry in norm.get('entries')or[]if isinstance(entry,dict)and entry.get('branch')!=br and(entry.get('mention_target')==br or entry.get('to')==br and not entry.get('tag')and bool(str(entry.get('text')or'').strip()))and not entry.get('acknowledged_at')]
def gate_pending_count(db):base=db.execute(f"SELECT (SELECT count(*) FROM notes WHERE acknowledged_at IS NULL AND blocking = 1) + (SELECT count(*) FROM submissions WHERE {PENDING_SUBMISSIONS})").fetchone()[0];return base+len(pending_board_entries_for_db(db))
def pr_state_warning(line):
	def payload(text):
		if text.startswith('@'):
			try:text=Path(text[1:]).read_text(encoding='utf-8')
			except(OSError,UnicodeError):return{}
		try:value=json.loads(text);return value if isinstance(value,dict)else{}
		except(ValueError,TypeError):return{}
	def api_write(words,client):
		method,endpoint,data='','',{};has_body=False;index=0
		while index<len(words):
			word=words[index];option,equal,value=word.partition('=')
			if option in{'-X','--method','--request','-d','--data','--data-raw','--data-binary','--json','--post-data','--body-data','--input','--post-file','--body-file'}|({'-f','-F','--field','--raw-field'}if client=='gh'else set()):
				if not equal:index+=1;value=words[index]if index<len(words)else''
				has_body=has_body or option not in{'-X','--method','--request'}
				if option in{'-X','--method','--request'}:method=value.upper()
				elif option in{'-f','-F','--field','--raw-field'}:
					key,separator,field=value.partition('=')
					if separator:
						if option in{'-F','--field'}and field.startswith('@')and field!='@-':
							try:field=Path(field[1:]).read_text(encoding='utf-8')
							except(OSError,UnicodeError):field=''
						keys=[key for key in re.split('\\[|\\]',key)if key];target=data
						for key in keys[:-1]:
							target=target.setdefault(key,{})
							if not isinstance(target,dict):break
						if keys and isinstance(target,dict):
							try:target[keys[-1]]=json.loads(field)
							except ValueError:target[keys[-1]]=field
				elif option in{'--input','--post-file','--body-file'}:
					if value!='-':data.update(payload('@'+value))
				else:data.update(payload(value))
			elif len(word)>2 and word[:2]in{'-X','-d'}|({'-f','-F'}if client=='gh'else set()):words=[word[:2],word[2:],*words[index+1:]];index=0;continue
			elif word.startswith(('https://','http://')):
				url=urlsplit(word)
				if client=='gh'or url.hostname=='api.github.com':endpoint=url.path.strip('/');data.update({key:values[-1]for(key,values)in parse_qs(url.query).items()})
			elif client=='gh'and(word=='graphql'or word.lstrip('/').startswith('repos/')):endpoint=word.strip('/').split('?',1)[0]
			index+=1
		endpoint=unquote(endpoint);method=method or('POST'if has_body else'GET')
		if endpoint=='graphql':
			query=data.get('query','')
			if not isinstance(query,str):return False
			query=re.sub('"""[\\s\\S]*?"""|"(?:\\\\.|[^"\\\\])*"','""',query);query=re.sub('#[^\\n]*','',query)
			if re.search('\\b(?:closePullRequest|reopenPullRequest|mergePullRequest|enablePullRequestAutoMerge|enqueuePullRequest|deleteRef|updateRef|mergeBranch|createCommitOnBranch)\\s*\\(',query):return True
			if re.search('\\bupdatePullRequest\\s*\\(',query):
				if re.search('\\bstate\\s*:\\s*(?:CLOSED|OPEN)\\b',query):return True
				variables=data.get('variables',{})
				if isinstance(variables,str):variables=payload(variables)
				def state_input(value):return isinstance(value,dict)and any(key=='state'and item in('CLOSED','OPEN')or state_input(item)for(key,item)in value.items())
				if isinstance(variables,dict)and any(state_input(variables.get(name))for name in re.findall('\\binput\\s*:\\s*\\$(\\w+)',query)):return True
				if isinstance(variables,dict):return any(variables.get(name)in('CLOSED','OPEN')for name in re.findall('\\bstate\\s*:\\s*\\$(\\w+)',query))
			return False
		if method=='GET'or method=='HEAD':return False
		if re.fullmatch('repos/[^/]+/[^/]+/pulls/\\d+/merge(?:-async)?',endpoint):return True
		if re.fullmatch('repos/[^/]+/[^/]+/merges',endpoint):return True
		if re.match('repos/[^/]+/[^/]+/git/refs/heads/',endpoint):return method in{'DELETE','PATCH'}
		return bool(method=='PATCH'and re.fullmatch('repos/[^/]+/[^/]+/(?:pulls|issues)/\\d+',endpoint)and data.get('state')in('closed','open'))
	def blocked(words):
		while words:
			executable=Path(words[0]).name
			if'='in words[0]and not words[0].startswith('-'):words=words[1:]
			elif executable in{'command','builtin','exec','env','sudo'}:
				if executable=='command'and any(word in{'-v','-V'}for word in words[1:]):return False
				words=words[1:]
				while words and words[0].startswith('-'):
					option=words.pop(0)
					if option in{'-u','--unset','--user','-g','--group'}and words:words.pop(0)
			else:break
		if not words:return False
		executable,arguments=Path(words[0]).name,words[1:]
		if executable in{'bash','sh','dash','zsh','ksh'}:
			for(index,word)in enumerate(arguments[:-1]):
				if word.startswith('-')and'c'in word:return script_write(arguments[index+1])
			return False
		if executable=='git':
			if'push'not in arguments:return False
			position=arguments.index('push');prefix,remaining=arguments[:position],arguments[position+1:];targets,destructive,read_only,remote_option,tags_only=[],False,False,False,False;index=0
			while index<len(remaining):
				word=remaining[index];option,equal,_=word.partition('=')
				if option in{'-o','--push-option','--receive-pack','--exec','--repo'}:
					remote_option=remote_option or option=='--repo'
					if not equal:index+=1
				elif word in{'--help','-h','--dry-run','-n'}:read_only=True
				elif option in{'--delete','-d','--mirror','--all','--prune'}:destructive=True
				elif word=='--tags':tags_only=True
				elif not word.startswith('-'):targets.append(word)
				index+=1
			if read_only:return False
			if destructive:return True
			refs=targets if remote_option else targets[1:]
			if refs:return any(word.lstrip('+').startswith(':')or word.rsplit(':',1)[-1].removeprefix('refs/heads/').lstrip('+')=='main'for word in refs)
			if tags_only:return False
			try:branch=subprocess.run(['git',*prefix,'symbolic-ref','--quiet','--short','HEAD'],capture_output=True,text=True,check=False);return branch.returncode==0 and branch.stdout.strip()=='main'
			except OSError:return False
		if executable=='gh':
			while arguments and arguments[0].startswith('-'):
				option=arguments.pop(0)
				if option in{'--help','-h','--version'}:return False
				if option in{'-R','--repo','--hostname'}and arguments:arguments.pop(0)
			if len(arguments)>=2 and arguments[0]=='pr':
				if arguments[1]not in{'close','reopen','merge'}:return False
				value_flags={'--comment','--body','--subject','--match-head-commit','--author-email','--repo','-R','-b'};return not any(word in{'--help','-h'}and(index==0 or arguments[index-1]not in value_flags)for(index,word)in enumerate(arguments))
			return bool(arguments and arguments[0]=='api'and api_write(arguments[1:],'gh'))
		return executable in{'curl','wget'}and api_write(arguments,executable)
	def script_write(text):
		start,quote,escaped=0,'',False
		for(index,char)in enumerate(text):
			if escaped:escaped=False
			elif char=='\\'and quote!="'":escaped=True
			elif quote:
				if char==quote:quote=''
			elif char in'"\'':quote=char
			elif char in';&|\n':
				if blocked(shlex.split(text[start:index])):return True
				start=index+1
		return blocked(shlex.split(text[start:]))
	try:
		if blocked(shlex.split(line)):return'PR STATE BLOCKED: visible PR-state write; use reads or metadata edits.'
	except ValueError:return None
	return None
def main_identical(root='.'):
	def git(*arguments):return subprocess.run(['git',*arguments],cwd=root,capture_output=True,text=True,check=False)
	try:
		for ref in('origin/main','HEAD'):
			if git('rev-parse','--verify','--quiet',ref).returncode:return False
		return git('diff','--quiet','origin/main','HEAD','--').returncode==0
	except OSError:return False
def skills_only_main_diff(root='.',base_ref='HEAD',target_ref='origin/main'):
	if not base_ref or not target_ref:return False
	try:proc=subprocess.run(['git','diff','--name-only',f"{base_ref}..{target_ref}"],cwd=root,capture_output=True,text=True,check=False,timeout=30)
	except(OSError,subprocess.TimeoutExpired):return False
	if proc.returncode!=0:return False
	paths=[line.strip()for line in proc.stdout.splitlines()if line.strip()];return bool(paths)and all(path.startswith('.agents/skills/')for path in paths)
def main_drift_warning(root='.',fresh=True):
	def git(*arguments):return subprocess.run(['git',*arguments],cwd=root,capture_output=True,text=True,check=False,timeout=30,env={**os.environ,'GIT_TERMINAL_PROMPT':'0'})
	def unavailable(reason):return f"Remote branch check unavailable: {reason}; behind count is unknown."
	try:
		if git('rev-parse','--git-dir').returncode:return''
		remote=git('remote','get-url','origin')
		if fresh and remote.returncode==0:
			branch=git('symbolic-ref','--quiet','--short','HEAD').stdout.strip()
			if not branch:return unavailable('detached HEAD')
			shallow=git('rev-parse','--is-shallow-repository').stdout.strip()=='true';main_ref='refs/remotes/origin/main';branch_ref=f"refs/remotes/origin/{branch}";refs=list(dict.fromkeys([f"+refs/heads/main:{main_ref}",f"+refs/heads/{branch}:{branch_ref}"]));arguments=['fetch','--quiet','--no-tags']
			if shallow:arguments.append('--unshallow')
			fetched=git(*arguments,'origin',*refs)
			if fetched.returncode:return unavailable(f"origin fetch failed (exit {fetched.returncode})")
			if git('rev-parse','--is-shallow-repository').stdout.strip()!='false':return unavailable('history is still shallow')
			if git('merge-base',main_ref,branch_ref).returncode:return unavailable('main and branch have no shared history')
			counts=git('rev-list','--left-right','--count',f"{main_ref}...{branch_ref}")
			if counts.returncode:return unavailable('cannot compare remote tips')
			behind,_ahead=map(int,counts.stdout.split())
			if git('diff','--quiet',main_ref,branch_ref,'--').returncode==0:
				if behind:return f"Remote branch {branch} and origin/main have identical trees; do not replay those commits. Start new work from main."
			elif behind:
				if skills_only_main_diff(root,branch_ref,main_ref):return f"Remote branch {branch} is {behind} commit(s) behind origin/main (`.agents/skills/**` sync only; no regate needed)."
				return f"Remote branch {branch} is {behind} commit(s) behind origin/main. Check whether main absorbed its commits before rebasing."
		if main_identical(root):return''
		if git('rev-parse','--is-shallow-repository').stdout.strip()=='true':
			visible=git('rev-list','--count','origin/main').stdout.strip()
			if visible in{'','0','1'}:return''
		count=git('rev-list','--count','HEAD..origin/main')
		if count.returncode==0 and int(count.stdout.strip())>0:
			if skills_only_main_diff(root,'HEAD','origin/main'):return f"Local check: origin/main carries {count.stdout.strip()} commit(s) this branch lacks (`.agents/skills/**` sync only; no regate needed)."
			return f"Local check: origin/main carries {count.stdout.strip()} commit(s) this branch lacks. Replay your commits over main: run git fetch origin and git rebase origin/main, then push."
		return''
	except subprocess.TimeoutExpired:return unavailable('Git timed out')
	except(OSError,ValueError):return unavailable('cannot read Git state')
def main_ancestor_warning(root='.'):
	def git(*arguments):return subprocess.run(['git',*arguments],cwd=root,capture_output=True,text=True,check=False,timeout=30,env={**os.environ,'GIT_TERMINAL_PROMPT':'0'})
	try:
		if git('rev-parse','--git-dir').returncode:return''
		if git('remote','get-url','origin').returncode:return''
		main_ref='refs/remotes/origin/main';arguments=['fetch','--quiet','--no-tags']
		if git('rev-parse','--is-shallow-repository').stdout.strip()=='true':arguments.append('--unshallow')
		if git(*arguments,'origin',f"+refs/heads/main:{main_ref}").returncode:return''
		if git('rev-parse','--verify','--quiet',main_ref).returncode:return''
		if git('merge-base','--is-ancestor',main_ref,'HEAD').returncode!=1:return''
		if skills_only_main_diff(root,'HEAD',main_ref):return''
		return'PUSH BLOCKED: `origin/main` is not an ancestor of HEAD, so the push replays onto a stale base. Run `git fetch origin` and `git rebase origin/main`, then push again.'
	except subprocess.TimeoutExpired:return''
	except(OSError,ValueError):return''
def repo_slug(root='.'):
	try:url=subprocess.run(['git','config','--get','remote.origin.url'],cwd=root,capture_output=True,text=True,check=False).stdout.strip()
	except OSError:return''
	match=re.search('[:/]([^/:]+/[^/]+?)(?:\\.git)?$',url);return match.group(1)if match else''
FAILED_CONCLUSIONS='failure','cancelled','timed_out'
PR_CHECK_POLL_SECONDS=10
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
			name,_,rest=row.partition('\t');stamp,_,conclusion=rest.partition('\t');rank=1 if not conclusion else 0,stamp.rstrip('Z')
			if name and(name not in newest or rank>newest[name][0]):newest[name]=rank,conclusion
		any_active=any(not conclusion for(_,conclusion)in newest.values());failed=[name for(name,(_,conclusion))in newest.items()if conclusion in FAILED_CONCLUSIONS and not(any_active and conclusion=='cancelled')];return', '.join(failed)
	except OSError:return None
def pr_check_once(result,root):
	failed=pr_failed_check(root)
	if failed:result.append(failed)
def pr_check_watch(result,root,stopped):
	while not stopped.is_set():
		pr_check_once(result,root)
		if result:return
		stopped.wait(PR_CHECK_POLL_SECONDS)
PR_CHECK_DISCOVERY_SECONDS=30
PR_CHECK_DISCOVERY_STEP=5
PR_CHECK_SUCCESS_STATES='SUCCESS','SKIPPED','NEUTRAL'
PR_CHECK_FAILURE_STATES='ACTION_REQUIRED','CANCELLED','ERROR','FAILURE','STALE','STARTUP_FAILURE','TIMED_OUT'
PR_STEP_LIMIT=5
PR_STEP_TIMEOUT=10
JOB_LINK_RE=re.compile('/actions/runs/\\d+/job/(\\d+)')
def pr_head_sha(root,branch,slug):
	try:listed=subprocess.run(['gh','pr','list','--head',branch,'--json','headRefOid','--jq','.[0].headRefOid'],cwd=root,capture_output=True,text=True,check=False,timeout=PR_CHECK_DISCOVERY_STEP)
	except(OSError,subprocess.TimeoutExpired):return None
	if listed.returncode:return None
	return listed.stdout.strip()
def gh_check_json(root,arguments):
	try:
		result=subprocess.run(['gh',*arguments],cwd=root,capture_output=True,text=True,check=False,timeout=PR_CHECK_DISCOVERY_STEP)
		if result.returncode:return None
		return[json.loads(line)for line in result.stdout.splitlines()if line.strip()]
	except(OSError,subprocess.TimeoutExpired,ValueError):return None
def pr_check_latest(root,slug,sha):
	owner,name=slug.split('/',1);query='query($owner:String!,$name:String!,$sha:String!,$endCursor:String){repository(owner:$owner,name:$name){object(expression:$sha){... on Commit{statusCheckRollup{contexts(first:100,after:$endCursor){pageInfo{hasNextPage endCursor} nodes{__typename ... on CheckRun{name status conclusion startedAt checkSuite{workflowRun{workflow{name}}}} ... on StatusContext{context state createdAt}}}}}}}}';projection='.data.repository.object.statusCheckRollup.contexts.nodes[]? | if .__typename == "CheckRun" then {type:"check",name,workflow:(.checkSuite.workflowRun.workflow.name // ""),state:(if .status == "COMPLETED" then .conclusion else .status end),at:.startedAt} else {type:"status",name:.context,workflow:"",state,at:.createdAt} end | @json';data=gh_check_json(root,['api','graphql','--paginate','-f',f"query={query}",'-f',f"owner={owner}",'-f',f"name={name}",'-f',f"sha={sha}",'--jq',projection])
	if not isinstance(data,list):return None
	latest={}
	for node in data:
		if not isinstance(node,dict)or not all(isinstance(node.get(field),str)for field in('type','name','workflow','state','at')):return None
		if node['type']not in('check','status')or not node['name']:return None
		try:stamp=datetime.fromisoformat(node['at'])
		except ValueError:return None
		if stamp.tzinfo is None:return None
		active=node['state']not in PR_CHECK_SUCCESS_STATES+PR_CHECK_FAILURE_STATES;rank=1 if active else 0,stamp;key=node['type'],node['name'],node['workflow']
		if key not in latest or rank>latest[key][0]:latest[key]=rank,node
	return[node for(_stamp,node)in sorted(latest.values(),key=lambda entry:(entry[1]['workflow'],entry[1]['name'],entry[1]['type']))]
def gh_api_lines(root,path,projection):
	try:result=subprocess.run(['gh','api',path,'--jq',projection],cwd=root,capture_output=True,text=True,check=False,timeout=PR_STEP_TIMEOUT)
	except(OSError,subprocess.TimeoutExpired):return None
	if result.returncode:return None
	return result.stdout.splitlines()
def pr_failed_steps(root='.'):
	slug=repo_slug(root);branch=workspace_branch(root)
	if not slug or not branch:return[]
	sha=pr_head_sha(root,branch,slug)
	if not sha:return[]
	rows=gh_api_lines(root,f"repos/{slug}/commits/{sha}/check-runs?per_page=100",'.check_runs[] | select(.conclusion=="failure") | [.name, .details_url] | @tsv');steps=[]
	for row in(rows or[])[:PR_STEP_LIMIT]:
		name,_,url=row.partition('\t');match=JOB_LINK_RE.search(url)
		if not name or not match:continue
		detail=gh_api_lines(root,f"repos/{slug}/actions/jobs/{match.group(1)}",'[.steps[] | select(.conclusion=="failure") | .name] | join(", ")')
		if detail and detail[0].strip():steps.append(f"{name} / {detail[0].strip()}")
	return steps
def pr_check_label(node):
	workflow=node['workflow']
	if workflow and workflow!=node['name']:return f"{workflow} / {node['name']}"
	return node['name']
def pr_queued_runs(root,slug,sha):
	if not slug or not sha:return[]
	try:result=subprocess.run(['gh','run','list','--repo',slug,'--commit',sha,'--json','name,status'],cwd=root,capture_output=True,text=True,check=False)
	except OSError:return[]
	if result.returncode!=0:return[]
	try:rows=json.loads(result.stdout or'[]')
	except json.JSONDecodeError:return[]
	if not isinstance(rows,list):return[]
	return[str(row.get('name')or'workflow')for row in rows if isinstance(row,dict)and str(row.get('status')or'').lower()in{'queued','in_progress','waiting','requested','pending'}]
def pr_checks_watch(root='.',seconds=PR_CHECK_DISCOVERY_SECONDS,step=PR_CHECK_DISCOVERY_STEP,sleep=None,progress=None):
	pause=sleep or time.sleep
	def unavailable(reason):return'unavailable',[f"PR CHECKS UNAVAILABLE: {reason}."]
	branch=workspace_branch(root);slug=repo_slug(root)
	if not branch or not slug:return unavailable('no branch pull request to watch')
	pinned='';started=.0;seen_any=False;reported=set();queued_reported=set();lines=[]
	while True:
		head=pr_head_sha(root,branch,slug)
		if head is None:return unavailable('gh did not read the pull request head')
		if not head:return unavailable('no branch pull request to watch')
		if head!=pinned:pinned=head;started=time.monotonic();seen_any=False;reported=set();queued_reported=set();lines=[]
		latest=pr_check_latest(root,slug,pinned)
		if latest is None:return unavailable('gh did not read the check rollup')
		elapsed=time.monotonic()-started
		if latest:seen_any=True
		active=any(node['state']not in PR_CHECK_SUCCESS_STATES+PR_CHECK_FAILURE_STATES for node in latest);failed=[node for node in latest if node['state']in PR_CHECK_FAILURE_STATES and not(active and node['state']=='CANCELLED')]
		for node in failed:
			identity=node['type'],node['workflow'],node['name'],node['at']
			if identity in reported:continue
			reported.add(identity);line=f"Failed so far: {pr_check_label(node)}";lines.append(line)
			if progress is not None:progress(line)
		if not seen_any:
			if elapsed>=seconds:
				queued=pr_queued_runs(root,slug,pinned)
				if queued:
					queued_key=tuple(queued)
					if queued_key not in queued_reported:
						queued_reported.add(queued_key);queued_line=f"Queued on {pinned[:7]}: {', '.join(queued)}";lines.append(queued_line)
						if progress is not None:progress(queued_line)
					if elapsed<max(seconds*2,seconds+step):pause(step);continue
					return'not_started',[*lines,f"PR CHECKS QUEUED: {', '.join(queued)} still queued on {pinned[:7]} after {int(elapsed)} seconds."]
				return'not_started',[f"PR CHECKS NOT STARTED: no visible checks appeared on {pinned[:7]} within {seconds} seconds."]
		elif elapsed>=seconds and all(node['state']in PR_CHECK_SUCCESS_STATES+PR_CHECK_FAILURE_STATES for node in latest):
			head=pr_head_sha(root,branch,slug)
			if head is None:return unavailable('gh did not read the pull request head')
			if not head:return unavailable('no branch pull request to watch')
			if head==pinned:
				if failed:names=', '.join(pr_check_label(node)for node in failed);return'failure',[*lines,f"PR CHECKS FAILED: {names}."]
				return'success',[*lines,'Best effort: all visible checks passed; later checks may appear.']
			continue
		if elapsed<seconds:pause(min(step,seconds-elapsed))
		else:pause(step)
SESSION_ID_TABLES=('notes','note'),('submissions','answer'),('reports','report'),('tasks','task')
HEX7_RE=re.compile('[0-9a-f]{7}')
MINT_ATTEMPTS=16
DIFF_FILE_RE=re.compile('^\\+\\+\\+ b/(.+)$')
DIFF_HUNK_RE=re.compile('^@@ -\\S+ \\+(\\d+)')
def session_id_tokens(store,db=None):
	tokens={}
	with store.transaction(db,autosave=False)as conn:
		for(table,label)in SESSION_ID_TABLES:
			for row in conn.execute(f"SELECT id FROM {table}").fetchall():
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
		if path and path.endswith(('assets/mermaid.min.js','assets/markdown-it.min.js')):continue
		for(token,pattern)in patterns.items():
			if pattern.search(line):type,ident=tokens[token];hits.append((path,number,token,type,ident))
	return hits
def commit_id_report(hits):
	if not hits:return None
	lines=['COMMIT BLOCKED: the staged change cites a session-local ID that exists only in this session, so repository readers cannot resolve it.']
	for(path,number,token,type,ident)in hits:named=ident if token==ident else f"{token} of {ident}";lines.append(f"  {path}:{number}: {type} {named}")
	lines.append('Strip the citation, then commit again.');renames={ident for(_,_,_,type,ident)in hits if type in{'task','report'}}
	if renames:lines.append('If this is a name collision, rename the preview record, not repository content:');lines.extend(f"  arena-preview rename {shlex.quote(ident)} NEW-ID"for ident in sorted(renames));lines.append('Replace NEW-ID with an unused preview ID.')
	return'\n'.join(lines)
def new_id():return uuid.uuid4().hex[:6]
def seconds_since(value):
	parsed=import_stamp(value)
	if parsed is None:return None
	return(datetime.now(UTC)-parsed.astimezone(UTC)).total_seconds()
def clip_stamp(value):
	if not value:return value
	parsed=import_stamp(value)
	if parsed is not None:return parsed.astimezone(UTC).strftime('%Y-%m-%dT%H:%M:%S')
	return str(value)[:19]
def board_stamp(value=None):
	if not value:return datetime.now(UTC).strftime('%Y-%m-%dT%H:%M:%SZ')
	parsed=import_stamp(value)
	if parsed is not None:return parsed.astimezone(UTC).strftime('%Y-%m-%dT%H:%M:%SZ')
	text=str(value).strip()
	if len(text)>=19:return f"{text[:19]}Z"
	return datetime.now(UTC).strftime('%Y-%m-%dT%H:%M:%SZ')
def board_stamp_or_none(value):
	if not value:return None
	return board_stamp(value)
def board_stamp_ms(value):
	parsed=import_stamp(value)
	if parsed is None:return None
	return int(parsed.astimezone(UTC).timestamp()*1000)
def tool_activity(meta):
	name=meta.get(TOOL_NAME_META)
	if name not in TOOL_PROBE_NAMES:return None
	return{'tool':name,'at':clip_stamp(meta.get(TOOL_AT_META))}
def at_or_after(value,other):
	if not value or not other:return False
	left=import_stamp(value);right=import_stamp(other)
	if left is not None and right is not None:return int(left.astimezone(UTC).timestamp())>=int(right.astimezone(UTC).timestamp())
	return clip_stamp(value)>=clip_stamp(other)
def import_stamp(value):
	if not value:return None
	try:parsed=datetime.fromisoformat(str(value))
	except ValueError:return None
	if parsed.tzinfo is None:parsed=parsed.replace(tzinfo=UTC)
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
CLAUSE_ACK=re.compile('([\\w./-]+\\.md):([0-9]+):\\s*\\S')
def clause_ack_error(text,root=None):
	base=Path(root or Path.cwd());files=read_set_files(base,worker=True)
	if not files:base=Path.cwd();files=read_set_files(base,worker=True)
	for match in CLAUSE_ACK.finditer(text or''):
		cited=re.sub('^(?:\\./)+','',match.group(1));cited_name=Path(cited).name;line_no=int(match.group(2));candidates=[base/path for path in files if(path==cited or path.endswith(f"/{cited}")if'/'in cited else Path(path).name==cited_name)]
		for path in candidates:
			if line_no>0 and path.is_file()and line_no<=len(path.read_text(encoding='utf-8').splitlines()):return''
	return'An ack of a violation note must name the clause as `file:ln: clause`, for example `ARENA.md:41: MUST read the bible`. The note stays pending.'
def when(value):
	try:datetime.fromisoformat(str(value))
	except ValueError as error:raise ValueError(f"Not a timestamp: {value!r}")from error
	return str(value)
def restore_receipt(acknowledged_at,ack_type,ack_text,ack_edited_at=None):
	carried=acknowledged_at,ack_type,ack_text
	if all(value is None for value in carried):
		if ack_edited_at is not None:raise ValueError('An edited receipt needs an acknowledgement')
		return None,None,None,None
	if any(value is None for value in carried):raise ValueError('A restored receipt carries its stamp, type and text, or none of them')
	if ack_type not in{'note','reply'}:raise ValueError('Every acknowledgement is a note or a reply, with its text')
	return when(acknowledged_at),ack_type,note_text(ack_text),when(ack_edited_at)if ack_edited_at is not None else None
def restore_replies(replies,acknowledged):
	if not replies:return None
	if not acknowledged:raise ValueError('Appended replies need an acknowledgement')
	if not isinstance(replies,list):raise TypeError('Appended replies are a list')
	checked=[]
	for item in replies:
		if not isinstance(item,dict)or item.get('type')not in{'note','reply'}:raise ValueError('Every appended reply is a note or a reply, with its text and stamp')
		checked.append({'type':item['type'],'text':note_text(item.get('text')),'at':when(item.get('at'))})
	return json.dumps(checked,ensure_ascii=False)
def replies_list(value):return json.loads(value)if value else[]
def message_row(row):
	record=dict(row);record.pop('seq',None)
	if'answer_data'in record:
		data=record.pop('answer_data')
		if data is not None:record['answer_data']=json.loads(data)
	if'replies'in record:record['replies']=replies_list(record['replies'])
	return record
def reply_reference(text):match=REPLY_REFERENCE.match(text or'');return match.group(1)if match else None
def reply_thread(record,notes):
	thread,seen=[],{record['id']};target=reply_reference(record.get('text'))
	while target:
		parent=notes.get(target)
		if parent is None and'/'in target:
			report_id,question_id=target.split('/',1);candidate=notes.get(report_id)
			if candidate is not None and candidate.get('type')=='report':parent=candidate|{'question_id':question_id}
		if parent is None:
			matches=[note for(note_id,note)in notes.items()if note_id.startswith(target)]
			if len(matches)!=1:break
			parent=matches[0]
		parent_id=parent['id']
		if parent_id in seen:break
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
	base=re.sub('[^a-z0-9]+','-',text.lower()).strip('-')[:60].rstrip('-')or'field';candidate,suffix=base,2
	while candidate in used:candidate=f"{base}-{suffix}";suffix+=1
	used.add(candidate);return candidate
def prompt_text(line):text=re.sub('^\\s*(?:[-*+]\\s+|#+\\s+|>\\s+|\\d+[.)]\\s+)','',line).strip();return text.strip('*_` ').rstrip(':').strip()
def custom_label(option):found=BLANK.match(option);return None if not found else prompt_text(found.group(1)or'')or'Other'
FORMAT_SLOT=re.compile('\\{([^{}]+)\\}')
def validate_submission_format(value,fields):
	if value is None:return None
	if not isinstance(value,dict)or set(value)-{'fields','template'}:raise ValueError('Submission format takes fields and an optional template')
	if len(json.dumps(value,ensure_ascii=False).encode('utf-8'))>MAX_REPORT:raise ValueError('Submission format exceeds the report size limit')
	known={field['id']:field for field in fields};mappings=value.get('fields',{})
	if not isinstance(mappings,dict)or set(mappings)-set(known):raise ValueError('Submission format names unknown fields')
	slots={f"{field_id}.{part}"for field_id in known for part in('question','answer')}
	def template(text,allowed):
		if not isinstance(text,str)or not text.strip():raise ValueError('A submission template is nonblank text')
		if set(FORMAT_SLOT.findall(text))-allowed:raise ValueError('Submission template contains an unknown placeholder')
	for(field_id,mapping)in mappings.items():
		if not isinstance(mapping,dict)or set(mapping)-{'question','answers','custom_answer'}:raise ValueError('Field format takes question, answers, and custom_answer')
		if'question'in mapping and(not isinstance(mapping['question'],str)or not 1<=len(mapping['question'].strip())<=500):raise ValueError('A mapped question is 1–500 characters')
		presets={option for option in known[field_id].get('options',[])if custom_label(option)is None};answers=mapping.get('answers',{})
		if not isinstance(answers,dict)or set(answers)-presets:raise ValueError('Answer maps name preset choices only')
		if any(not isinstance(text,str)or not text.strip()for text in answers.values()):raise ValueError('Mapped preset answers are nonblank text')
		if'custom_answer'in mapping:template(mapping['custom_answer'],{'custom'})
	if'template'in value:template(value['template'],slots)
	return value
def expand_submission_template(text,values):
	parts,size,position=[],0,0
	for match in FORMAT_SLOT.finditer(text):
		for part in(text[position:match.start()],values[match.group(1)]):
			size+=len(part.encode('utf-8'))
			if size>MAX_REPORT:raise ValueError('Expanded submission exceeds the report size limit')
			parts.append(part)
		position=match.end()
	tail=text[position:]
	if size+len(tail.encode('utf-8'))>MAX_REPORT:raise ValueError('Expanded submission exceeds the report size limit')
	return''.join(parts)+tail
def formatted_selection(field,item,mapping):
	presets=[option for option in field['options']if custom_label(option)is None];labels={custom_label(option)for option in field['options']}-{None}
	if isinstance(item,str):
		if mapping is None and item in field['options']:label=custom_label(item);return('custom',label)if label is not None else('preset',item),item
		if item in presets:item={'type':'preset','value':item}
		else:
			for label in labels:
				if item.startswith(f"{label}: "):item={'type':'custom','label':label,'value':item[len(label)+2:]};break
	if not isinstance(item,dict)or set(item)-{'type','value','label'}or not isinstance(item.get('value'),str):raise ValueError(f"{field['id']}: choose a preset or enter a custom answer")
	text=item['value']
	if item.get('type')=='preset'and set(item)=={'type','value'}and text in presets:return('preset',text),mapping.get('answers',{}).get(text,text)if mapping is not None else text
	if item.get('type')=='custom'and set(item)=={'type','label','value'}and item['label']in labels and text.strip():
		if mapping is None:return('custom',item['label']),f"{item['label']}: {text}"
		wrapper=mapping.get('custom_answer');return('custom',item['label']),expand_submission_template(wrapper,{'custom':text})if wrapper is not None else text
	raise ValueError(f"{field['id']}: invalid selection or blank custom answer")
def iter_md_comments(text):
	if'<!--'not in text:return
	runs_by_len,offsets={},{};n=len(text)
	if'`'in text:
		pos=text.find('`')
		while pos!=-1:
			end_pos=pos+1
			while end_pos<n and text[end_pos]=='`':end_pos+=1
			runs_by_len.setdefault(end_pos-pos,[]).append((pos,end_pos));pos=text.find('`',end_pos)
	cursor=0
	while cursor<n:
		c_start=text.find('<!--',cursor)
		if c_start==-1:break
		t_start=text.find('`',cursor)if runs_by_len else-1
		if t_start==-1 or c_start<t_start:
			c_end=text.find('-->',c_start+4)
			if c_end==-1:break
			yield(c_start,c_end+3);cursor=c_end+3;continue
		t_end=t_start+1
		while t_end<n and text[t_end]=='`':t_end+=1
		run_len=t_end-t_start;spans=runs_by_len.get(run_len,());idx=offsets.get(run_len,0)
		while idx<len(spans)and spans[idx][0]<t_end:idx+=1
		if idx<len(spans):offsets[run_len]=idx+1;cursor=spans[idx][1]
		else:offsets[run_len]=idx;cursor=t_end
def strip_md_comments(text,keep_anchors=False):
	if'<!--'not in text:return text
	out,last=[],0
	for(start,end)in iter_md_comments(text):
		if keep_anchors and COMMENT_ANCHOR.fullmatch(text[start:end].strip()):continue
		out.append(text[last:start]);last=end
	if not out and last==0:return text
	out.append(text[last:]);return''.join(out)
def find_comment_anchor(text):
	found=None
	for(start,end)in iter_md_comments(text):
		anchor_match=COMMENT_ANCHOR.fullmatch(text[start:end].strip())
		if anchor_match:found=anchor_match.group(1)
	return found
def strip_fenced_md_comments(markdown,keep_anchors=False):
	lines=markdown.splitlines(keepends=True);out,chunk,fence=[],[],None
	def flush():
		if not chunk:return
		out.append(strip_md_comments(''.join(chunk),keep_anchors=keep_anchors));chunk.clear()
	for line in lines:
		if fence is not None:
			out.append(line)
			if line.strip().startswith(fence):fence=None
			continue
		opening=FENCE.match(line)
		if opening:flush();fence=opening.group(1);out.append(line);continue
		chunk.append(line)
	flush();return''.join(out)
def _replace_mark_outside_inline_code(text):
	if'<mark'not in text.lower():return text
	runs_by_len,offsets={},{};n=len(text)
	if'`'in text:
		pos=text.find('`')
		while pos!=-1:
			end_pos=pos+1
			while end_pos<n and text[end_pos]=='`':end_pos+=1
			runs_by_len.setdefault(end_pos-pos,[]).append((pos,end_pos));pos=text.find('`',end_pos)
	out,cursor=[],0;lower=text.lower()
	while cursor<n:
		m_start=lower.find('<mark>',cursor)
		if m_start==-1:out.append(text[cursor:]);break
		t_start=text.find('`',cursor)if runs_by_len else-1
		if t_start!=-1 and t_start<m_start:
			t_end=t_start+1
			while t_end<n and text[t_end]=='`':t_end+=1
			run_len=t_end-t_start;spans=runs_by_len.get(run_len,());idx=offsets.get(run_len,0)
			while idx<len(spans)and spans[idx][0]<t_end:idx+=1
			if idx<len(spans):offsets[run_len]=idx+1;out.append(text[cursor:spans[idx][1]]);cursor=spans[idx][1]
			else:offsets[run_len]=idx;out.append(text[cursor:t_end]);cursor=t_end
			continue
		m_end=lower.find('</mark>',m_start+6);nl=text.find('\n',m_start+6)
		if m_end==-1 or nl!=-1 and nl<m_end:out.append(text[cursor:m_start+6]);cursor=m_start+6;continue
		out.append(text[cursor:m_start]);inner=text[m_start+6:m_end];core=inner.strip()
		if core:lead=inner[:len(inner)-len(inner.lstrip())];tail=inner[len(inner.rstrip()):];out.append(f"{lead}=={core}=={tail}")
		else:out.append(inner)
		cursor=m_end+7
	return''.join(out)
def normalize_report_mark_tags(markdown):
	if'<mark'not in markdown.lower():return markdown
	lines=markdown.splitlines(keepends=True);out,chunk,fence=[],[],None
	def flush():
		if not chunk:return
		out.append(_replace_mark_outside_inline_code(''.join(chunk)));chunk.clear()
	for line in lines:
		if fence is not None:
			out.append(line)
			if line.strip().startswith(fence):fence=None
			continue
		opening=FENCE.match(line)
		if opening:flush();fence=opening.group(1);out.append(line);continue
		chunk.append(line)
	flush();return''.join(out)
def option_continuation(line):
	cleaned=strip_md_comments(line)
	if not cleaned.strip()or BLOCK_START.match(cleaned):return False
	return not BLANK.match(cleaned)and not ANCHOR.match(cleaned)
def parse_fields(markdown):
	lines=strip_fenced_md_comments(markdown,keep_anchors=True).splitlines();blocks,chunk,questions,used=[],[],[],set();fence,prompt,anchor,index,position=None,'',None,0,0
	while position<len(lines):
		line=lines[position]
		if fence is not None:
			chunk.append(line)
			if line.strip().startswith(fence):fence=None
			position+=1;continue
		opening=FENCE.match(line)
		if opening:fence=opening.group(1);chunk.append(line);position+=1;continue
		comment_anchor=find_comment_anchor(line);cleaned=strip_md_comments(line)
		if not cleaned.strip()and line.strip():
			if comment_anchor:anchor=comment_anchor
			position+=1;continue
		type='choice'if CHOICE.match(cleaned)else'checkbox'if CHECKBOX.match(cleaned)else None;blank=None if type else BLANK.match(cleaned)
		if not type and not blank:
			chunk.append(cleaned)
			if cleaned.strip():
				prompt=prompt_text(ANCHOR.sub('',cleaned));found=ANCHOR.search(cleaned);anchor=found.group(1)if found else comment_anchor
				if found or comment_anchor:chunk[-1]=ANCHOR.sub('',cleaned).rstrip()
			position+=1;continue
		index+=1
		if type:
			pattern=CHOICE if type=='choice'else CHECKBOX;options,default=[],[]
			while position<len(lines):
				item=pattern.match(strip_md_comments(lines[position]))
				if not item:break
				preselected=item.group(1).lower()=='x';options.append(item.group(2))
				if preselected:default.append(item.group(2))
				position+=1
				while position<len(lines)and option_continuation(lines[position]):
					options[-1]=f"{options[-1]} {strip_md_comments(lines[position]).strip()}"
					if preselected:default[-1]=options[-1]
					position+=1
			if len(set(options))!=len(options):raise ValueError(f"Field '{prompt or index}' repeats an option; make each unique")
			question={'type':type,'options':options,'default':default}
		else:
			label=(blank.group(1)or'').strip();question={'type':'text','default':[]}
			if label:prompt=prompt_text(ANCHOR.sub('',label));found=ANCHOR.search(label);anchor=found.group(1)if found else comment_anchor
			position+=1
		question['prompt']=prompt or f"Field {index}";question['id']=anchor if anchor and anchor not in used else slug(question['prompt'],used);used.add(question['id']);anchor=None;blocks.append(('markdown','\n'.join(chunk)));blocks.append(('field',question));chunk=[];questions.append(question)
	blocks.append(('markdown','\n'.join(chunk)))
	if questions:
		Store.validate_fields(questions)
		for question in questions:
			if question['type']!='text'and not any(custom_label(option)is not None for option in question['options']):question['options'].append('Something else...: ___')
	return blocks,questions
def render_inline(markdown):
	markdown=unescape_fences(strip_md_comments(markdown))
	try:from markdown_it import MarkdownIt
	except ImportError:return html.escape(markdown)
	parser=MarkdownIt('commonmark',{'html':False}).enable(['strikethrough']);highlight_markdown(parser);parser.add_render_rule('link_open',open_link);return parser.renderInline(markdown)
def field_html(question):
	prompt=html.escape(question['prompt'],quote=True);name=html.escape(question['id'],quote=True);body=f'<div class="question" id="report-question-{name}" data-field="{name}"';body+=f' data-type="{question["type"]}">';body+=f'<small class="question-id">Question ID: <code>{name}</code></small>'
	if question['type']=='text':return f'{body}<textarea class="answer-text" rows="2" placeholder="Answer" aria-label="{prompt}"></textarea></div>'
	control='radio'if question['type']=='choice'else'checkbox';group=f'<div class="options" role="group" aria-label="{prompt}">'
	for option in question['options']:
		value=html.escape(option,quote=True);recommended=option in question['default'];checked=' checked'if recommended else'';label=custom_label(option)
		if label is None:prefix='* 'if recommended else'';group+=f'<label class="option"><input type="{control}" name="{name}" value="{value}"{checked}> <span>{prefix}{render_inline(option)}</span></label>';continue
		named=html.escape(label,quote=True);group+=f'<label class="option"><input type="{control}" name="{name}" value="{value}"{checked} data-label="{named}" aria-label="{named}"><textarea class="custom-text" rows="1" data-custom="{named}" placeholder="Something else..." aria-label="{named}, your own answer"></textarea></label>'
	return f"{body}{group}</div></div>"
TASK_STATUSES='upcoming','finished'
TASK_UNSPECIFIED='unspecified'
TASK_MISC='misc'
TASK_TYPES='report','code','docs','ops','misc'
TASK_LEGACY_TYPES='operations',TASK_UNSPECIFIED
TASK_ID=re.compile('^[a-z0-9][a-z0-9-]{0,63}$')
NOTE_ID=re.compile('^[0-9a-f]{6,7}(?:-[0-9a-f]+)?$')
MAX_TASK_TITLE=200
MAX_TASK_DETAIL=2000
MAX_TASK_DETAILS=40
TASK_STEP_MAX=120
TASK_SCOPE_PREFIX='Scope: '
TASK_ORDER_OWNED_META='task_order_owned'
TASK_ORDER_PENDING_META='task_order_notice_pending'
TASK_ORDER_NOTICE='Task order changed in the preview; preserve its upcoming task order.'
ECHO_DETAIL=200
TASK_COLUMNS='id, title, details, status, position, updated_at, completed_at, blocked, report_id, type'
TASK_ORDER="status DESC, CASE WHEN status = 'finished' THEN completed_at END DESC, CASE WHEN status = 'finished' THEN id END DESC, position, id"
RENAME_TARGETS={'note':('notes',(('uploads','note_id'),)),'answer':('submissions',()),'report':('reports',(('submissions','report_id'),('tasks','report_id'))),'task':('tasks',(('notes','task_id'),('submissions','task_id')))}
RENAME_TABLES='notes','submissions','reports','tasks'
CLI_DESCRIPTION="Notes, reports, tasks and the preview server for Arena steering. The installed hook calls `pre`, `gate` and `route-run`; the rest are the agent's."
SAVED_STATE='saved-state.ndjson'
NOTE_LINE_KEYS='id','text','at','acknowledged_at','ack_type','ack_text','ack_edited_at','replies','ack_edited_seen_count','seen_at','task_id'
TASK_LINE_KEYS='id','title','details','status','completed_at','order','report_id','type'
SUBMISSION_LINE_KEYS='id','report_id','text','at','acknowledged_at','ack_type','ack_text','ack_edited_at','replies','ack_edited_seen_count','seen_at','task_id'
REPORT_LINE_KEYS='id','title','markdown','published_at','seen_at','ack_seen_at','pinned','source'
def task_row(row):
	shaped={'id':row[0],'title':row[1],'details':json.loads(row[2]),'status':row[3],'order':row[4],'updated_at':row[5],'completed_at':row[6]if len(row)>6 else None,'blocked':bool(row[7])if len(row)>7 else False}
	if len(row)>8 and row[8]:shaped['report_id']=row[8]
	shaped['type']=row[9]if len(row)>9 and row[9]else TASK_MISC;return shaped
def attach_blockers(db,records):
	rows=db.execute('SELECT task_id, blocked_by FROM task_dependencies ORDER BY blocked_by').fetchall();edges={}
	for row in rows:edges.setdefault(row[0],[]).append(row[1])
	for record in records:
		blockers=edges.get(record['id'])
		if blockers:record['blocked_by']=blockers
	return order_after_blockers(records)
def order_after_blockers(records):
	known={item['id']for item in records};waiting={item['id']:[blocker for blocker in item.get('blocked_by')or()if blocker in known]for item in records};placed,done,pending=[],set(),list(records)
	while pending:
		first=next((item for item in pending if all(b in done for b in waiting[item['id']])),None)
		if first is None:placed.extend(pending);break
		placed.append(first);done.add(first['id']);pending.remove(first)
	return placed
UNLINKED_TASK_ID=''
def link_task_answers(db,records):
	links=db.execute('SELECT submissions.id, submissions.report_id, submissions.task_id,\n              submissions.text, submissions.at, reports.title AS title\n       FROM submissions LEFT JOIN reports ON reports.id = submissions.report_id\n       ORDER BY submissions.at, submissions.seq').fetchall();answers=[{'id':link['id'],'at':clip_stamp(link['at']),'text':link['text'],'report_id':link['report_id'],'title':link['title']or'','task_id':link['task_id']}for link in links]
	for item in records:
		linked=[{'id':answer['id'],'at':answer['at'],'text':answer['text'],'report_id':answer['report_id'],'title':answer['title']}for answer in answers if answer['task_id']!=UNLINKED_TASK_ID and(answer['task_id']==item['id']or answer['report_id']and answer['report_id']==item.get('report_id'))]
		if linked:item['answers']=linked
	return records
def saved_note_line(record):
	if not isinstance(record,dict):raise TypeError('Every saved note is an object')
	return{key:record.get(key)for key in NOTE_LINE_KEYS}
def saved_answer_line(record):
	if not isinstance(record,dict):raise TypeError('Every saved answer is an object')
	line={key:record.get(key)for key in SUBMISSION_LINE_KEYS}
	if record.get('answer_data')is not None:line['answer_data']=record['answer_data']
	return line
def saved_report_line(record):
	if not isinstance(record,dict):raise TypeError('Every saved report is an object')
	line={key:record.get(key)for key in REPORT_LINE_KEYS}
	if isinstance(line.get('markdown'),str)and'/api/report-image?'in line['markdown']:line['markdown']=inline_report_image_urls(line['markdown'],Path(record['source']).resolve().parent if record.get('source')else None)
	if record.get('submission_format')is not None:line['submission_format']=record['submission_format']
	return line
def state_ndjson(lines):return''.join(json.dumps(line,ensure_ascii=False)+'\n'for line in lines)
def saved_task_line(record):
	if not isinstance(record,dict):raise TypeError('Every saved task is an object')
	line={key:record.get(key)for key in TASK_LINE_KEYS};line['details']=[str(item)for item in record.get('details')or[]];line['type']=record.get('type')or TASK_MISC
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
def stream_note_attachments(content_type,stream,length,staged=False):
	from email import policy;from email.parser import BytesParser
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
			name=part.get_param('name',header='content-disposition');allowed={'id','file'}if staged else{'id','text','file'}
			if part.defects or part.is_multipart()or part.get_content_disposition()!='form-data'or name not in allowed:raise ValueError('Unexpected note attachment field')
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
		if staged:
			if set(fields)!={'id'}or len(files)!=1:raise ValueError('Send one staged ID and one file')
			yield(fields['id'],*files[0]);return
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
	type=item.get('type')or'note';display_type='answer'if type=='report'else type;raw_id=str(item.get('id',''));header='`'+(raw_id if type=='board'else raw_id[:6])+'`'
	if display_type!='note':header+=f" {display_type}"
	if type=='board'and item.get('board_id'):header+=f" ({item['board_id']})"
	if item.get('quiet')and not item.get('blocking'):header+=' quiet (no ack required)'
	if item.get('report_id'):header+=f" report {item['report_id']}"
	lines=[header];text=item.get('text')or''
	if type=='report':text=format_task_answer(item)
	if text:lines.append(text)
	attachments=item.get('attachments')or[]
	if attachments:names=', '.join(attachment_line(record)for record in attachments);lines.append(f"attachments: {names}")
	elif type=='board'and isinstance(item.get('file'),dict):
		board_file=str(item['file'].get('staged_path')or item['file'].get('name')or'').strip()
		if board_file:lines.append(f"attachments: {board_file}")
	if item.get('patch_hint'):lines.append(str(item['patch_hint']))
	return'\n'.join(lines)
def format_pending_item(item):
	if not reply_reference(item.get('text')):return format_pending_record(item)
	context=''
	for parent in item.get('thread')or[]:
		if parent.get('type')=='report':source=parent.get('source')or f"/api/reports/{parent['id']}/source";target_id=f"{parent['id']}/{parent['question_id']}"if parent.get('question_id')else parent['id'];context=f"RE: {target_id}\nSource: {source}";continue
		body=REPLY_REFERENCE.sub('',parent.get('text')or'',count=1).lstrip('\r\n');body=format_pending_record(parent|{'text':body}).partition('\n')[2]
		if context:body=context+'\n\n'+body
		context='\n'.join('> '+line for line in body.split('\n'))
	body=REPLY_REFERENCE.sub('',item.get('text')or'',count=1).lstrip('\r\n');context=context or'Referenced message unavailable.';return format_pending_record(item|{'text':context+'\n\n'+body})
def format_read(listing):
	checked_at=listing.get('checked_at');pending=listing.get('pending')or[];count=len(pending)
	if checked_at:first=f"Inbox {local_stamp(checked_at)}: {count} pending"
	else:first=f"Inbox: {count} pending"
	if count==0:return first
	return'\n'.join([first,*(format_pending_item(item)for item in pending)])
def current_task(tasks):
	statuses={task['id']:task.get('status')for task in tasks}
	for task in tasks:
		if task.get('status')!='upcoming'or task.get('blocked'):continue
		if all(statuses.get(blocker)=='finished'for blocker in task.get('blocked_by')or()):return task
	return None
def format_task_answer(answer):
	text=str(answer.get('text')or'');report_id=str(answer.get('report_id')or'')
	if not report_id:return text
	first_line,separator,_=text.partition('\n');prefix=f"REPORT {report_id}";boundary=first_line[len(prefix):len(prefix)+1]
	if not separator or not first_line.startswith(prefix):return text
	if boundary not in(' ',':')or not first_line.endswith(':'):return text
	title=str(answer.get('title')or'').strip();return f"REPORT {title or report_id}:{text[len(first_line):]}"
def format_task_list(tasks,full=False):
	shown=tasks if full else[t for t in tasks if t.get('status')=='upcoming'];lines=[f"Tasks {len(shown)}"if full else f"Tasks {len(shown)} upcoming"]
	if any(t.get('blocked')for t in shown):lines.append('* = blocked on a report answer or linked run')
	if not full:shown=[t for t in shown if not t.get('blocked')]+[t for t in shown if t.get('blocked')]
	head=None if full else current_task(shown)
	if head is not None:shown=[head,*(t for t in shown if t.get('id')!=head.get('id'))]
	for task in shown:
		order=task.get('order');status=task.get('status');is_current=head is not None and task.get('id')==head.get('id')
		if is_current:status='current'
		id_=task.get('id');blocked=task.get('blocked');report_id=task.get('report_id');blocked_mark='*'if blocked else'';line=f"{order}{blocked_mark}  {status} {id_}"
		if report_id:line+=f" | waits on {report_id}"
		if task.get('runs'):line+=f" | runs {', '.join(task['runs'])}"
		if task.get('type')and task.get('type')!=TASK_UNSPECIFIED:line+=f" | type {task.get('type')}"
		lines.append(line)
		for detail in task.get('details',[]):lines.append(f"    {detail}")
		for answer in task.get('answers',[]):lines.append(f"    answer {answer['id']}, at {answer['at']}");lines.append(f"    {format_task_answer(answer)}")
		if is_current:lines.append('    Is this still the task you are working on?')
	return'\n'.join(lines)
def format_task(record,full=False):
	id_=record.get('id');order=record.get('order');status=record.get('status');report_id=record.get('report_id');blocked=record.get('blocked');title=record.get('title')or'';details=record.get('details')or[];cut=details if full else[detail[:ECHO_DETAIL]+('…'if len(detail)>ECHO_DETAIL else'')for detail in details];first=f"task {id_}, order {order}, status {status}";task_type=record.get('type')
	if task_type and task_type!=TASK_UNSPECIFIED:first+=f", type {task_type}"
	if report_id:first+=f", report {report_id}"
	if record.get('runs'):first+=f", runs {', '.join(record['runs'])}"
	if blocked:first+=', blocked'
	lines=[first,title]+[f"- {d}"for d in cut]
	if full:
		for answer in record.get('answers',[]):lines.extend([f"answer {answer['id']}, at {answer['at']}",format_task_answer(answer)])
	return'\n'.join(lines)
def print_task_row(store,task_id):
	if not store.id_matches(task_id):raise ValueError(f"No task is stored under {task_id}.")
	resolved=store.resolve_id(task_id);tasks=store.tasks()or{};record=next((item for status in TASK_STATUSES for item in tasks.get(status,[])if item['id']==resolved),None)
	if record is None:raise ValueError(f"No task is stored under {resolved}.")
	print(format_task(record,full=True))
def _countdown_duration(total_seconds):
	if total_seconds>=86400:return f"{total_seconds//86400}d"
	if total_seconds>=3600:return f"{total_seconds//3600}h"
	if total_seconds>=60:return f"{total_seconds//60}m"
	return f"{total_seconds}s"
def key_countdown(value,moment=None):
	stamp=import_stamp(value)
	if stamp is None:return f"at {local_stamp(value,moment)}"
	current=moment or datetime.now(UTC)
	if current.tzinfo is None:current=current.replace(tzinfo=UTC)
	remaining=int((stamp+timedelta(seconds=AGENT_KEY_TTL_SECONDS)-current).total_seconds())
	if remaining>=0:return f"expires in {_countdown_duration(remaining)}"
	return f"expired {_countdown_duration(-remaining)} ago"
def format_key(record,moment=None):
	lines=[]
	if record.get('key'):lines.append(f"key {record['key']}")
	if record.get('host'):lines.append(f"host {record['host']}");lines.append(f"use fetch_page on {record['host']}/v1/<route>?key=<key>, never bash")
	if record.get('at'):lines.append(key_countdown(record['at'],moment))
	if record.get('egress_version'):lines.append(f"egress {record['egress_version']}")
	if record.get('userscript_version'):lines.append(f"userscript {record['userscript_version']}")
	return'\n'.join(lines)if lines else''
def format_fetch_job(job):
	id_=job.get('id');url=job.get('url');allow_proxy=job.get('allow_proxy');approval=job.get('approval');status=job.get('status');line=f"Queued {id_} {url}"
	if allow_proxy is not None:line+=f" (proxy {'on'if allow_proxy else'off'})"
	if approval:line+=f", approval {approval}"
	if status:line+=f", status {status}"
	parts=[line]
	if approval=='pending':parts.append('Ask the owner to Approve it on the Preview page.')
	return'\n'.join(parts)
def format_import_receipt(receipt):notes=receipt.get('notes',0);answers=receipt.get('answers',0);reports=receipt.get('reports',0);tasks=receipt.get('tasks',0);return f"Imported notes {notes}, answers {answers}, reports {reports}, tasks {tasks}; the live state was replaced"
def format_clear_state(counts):parts=[f"{k} {v}"for(k,v)in counts.items()];return'Cleared '+', '.join(parts)if parts else'Cleared'
def format_state_usage(usage):total=f"State usage: {usage['bytes']} bytes in {usage['files']} files";cats=usage.get('categories')or{};details=[f"  {name}: {info['bytes']} bytes ({info['files']} files)"for(name,info)in cats.items()if info.get('files')];return'\n'.join([total,*details])
def format_clean_state(receipt):prefix='Would remove'if receipt.get('dry_run')else'Removed';head=f"{prefix} {receipt['removed_files']} file(s) ({receipt['removed_bytes']} bytes); remaining {receipt['usage']['bytes']} bytes in {receipt['usage']['files']} files";paths=[f"  {path}"for path in receipt.get('paths')or()];return'\n'.join([head,*paths])
def _state_record_category(record):
	if'markdown'in record:return'reports'
	if'title'in record:return'tasks'
	if'report_id'in record:return'answers'
	return'notes'
def resolve_compare_state_source(state_dir,source=None):
	if source is not None:return Path(source)
	uploads_dir=Path(state_dir)/'uploads'
	if uploads_dir.is_dir():
		candidates=[path for path in uploads_dir.iterdir()if path.is_file()and path.suffix.lower()in{'.ndjson','.json'}]
		if candidates:candidates.sort(key=lambda path:path.stat().st_mtime,reverse=True);return candidates[0]
	for candidate in(Path(state_dir)/'backups'/'state-latest.ndjson',Path(state_dir)/'backups'/'state-previous.ndjson',Path(state_dir)/'saved-state.ndjson'):
		if candidate.is_file():return candidate
	raise ValueError('Give a state file or upload one first')
def format_state_comparison(comparison,source=None):
	label=f" ({Path(source).name})"if source is not None else'';live_stamp=clip_stamp(comparison.get('live_stamp'))or'none';file_stamp=clip_stamp(comparison.get('file_stamp'))or'none';lines=[f"State compare{label}: {comparison['verdict']} (live {live_stamp}, file {file_stamp})"];groups=comparison.get('groups')or{}
	for name in('notes','answers','reports','tasks'):
		row=groups.get(name)or{};only_live=row.get('only_live')or[];only_file=row.get('only_file')or[];changed=row.get('changed')or[];line=f"{name}: live {row.get('live',0)} file {row.get('file',0)} +{len(only_live)} -{len(only_file)} ~{len(changed)}"
		if only_file:line+=f" file_only={','.join(only_file[:5])}"
		lines.append(line)
	return'\n'.join(lines)
def preview_server_available(store):
	port=store.meta_value('port')
	if not port:return False
	with closing(socket.socket())as probe:probe.settimeout(1);return probe.connect_ex(('127.0.0.1',int(port)))==0
def require_server(store):
	port=store.meta_value('port')
	if not port:raise ValueError('Preview down. Run arena-preview serve.')
	if preview_server_available(store):return
	raise ValueError(f"Preview down. Before polling: arena-preview serve --port {port}")
def require_survey(store):
	with closing(store.connect())as db:pending=db.execute("SELECT id FROM notes WHERE type = 'survey' AND acknowledged_at IS NULL LIMIT 1").fetchone()
	if pending is not None:raise ValueError("Survey unanswered. Read and acknowledge the survey note, then run `arena-preview survey '<json>'` before polling.")
	port=store.meta_value('port')
	if not port:return
	from http.client import HTTPConnection,HTTPException
	try:
		with closing(HTTPConnection('127.0.0.1',int(port),timeout=2))as client:
			client.request('GET','/api/survey');response=client.getresponse();body=response.read()
			if response.status!=200:return
			payload=json.loads(body.decode('utf-8'))
	except(HTTPException,OSError,UnicodeDecodeError,ValueError):return
	if isinstance(payload,dict)and payload.get('results')is None:raise ValueError("Survey unanswered. Run `arena-preview survey '<json>'` before polling.")
def print_read(store):
	for notice in store.take_board_notifications():print(notice,flush=True)
	listing=store.read();print(format_read(listing),flush=True)
	if listing['pending']:store.mark_seen([item['id']for item in listing['pending']]);store.mark_reports_agent_seen([item.get('report_id')for item in listing['pending']])
POLL_INTERVAL=1
POLL_MAX_LOOPS=1800
POLL_WAIT_SECONDS=1799
POLLING_META='polling_at'
POLL_SINCE_META='polling_since'
POLLING_FRESH_SECONDS=5.
POLL_REPEAT_SECONDS=45
POLL_REPEAT_META='poll_repeat'
POLL_IMMEDIATE_SECONDS=1.
POLL_IMMEDIATE_META='poll_immediate'
READ_SET_SLOT_META='read_set_slot'
READ_SET_LAST_META='read_set_last_at'
READ_SET_INTERVAL_MINUTES=15
SKIP_POLL_META='skip_poll_at'
def read_set_slot(moment=None):
	moment=moment or datetime.now(DISPLAY_ZONE or UTC)
	if moment.tzinfo is None:moment=moment.replace(tzinfo=UTC)
	minute=moment.minute//READ_SET_INTERVAL_MINUTES*READ_SET_INTERVAL_MINUTES;return moment.replace(minute=minute,second=0,microsecond=0).isoformat()
def read_set_paths(root=None,worker=False):
	root=Path(root or resolve_repo_root()).resolve();skill=root/'.agents'/'skills'/'arena-skill'/'SKILL.md';candidates=[root/'ARENA.md',root/'AGENTS.md',skill]
	if worker:
		for rel in('.agents/skills/arena-skill/WORKER.md','skills/arena-skill/WORKER.md'):
			candidate=root/rel
			if candidate.is_file():candidates.append(candidate);break
	return[path for path in candidates if path.is_file()]
def resolve_agents_chain(target=None,root=None):
	default_root=Path(root or resolve_repo_root()).resolve();raw=Path(target)if target is not None else default_root;resolved=(raw if raw.is_absolute()else default_root/raw).resolve()
	if not resolved.is_dir()and(resolved.is_file()or resolved.suffix):resolved=resolved.parent
	base=default_root
	if root is None and not resolved.is_relative_to(default_root):
		for candidate in reversed([resolved,*resolved.parents]):
			if any((candidate/marker).exists()for marker in('.git','ARENA.md','AGENTS.md')):base=candidate;break
	try:rel=resolved.relative_to(base)
	except ValueError as error:raise ValueError('Target path must be inside the repository root')from error
	current=base;chain=[current]
	for part in rel.parts:current=current/part;chain.append(current)
	return base,[directory/'AGENTS.md'for directory in chain if(directory/'AGENTS.md').is_file()]
INERT_COMMANDS='cd','mkdir','[','test','export','set','unset','true',':','source','.','trap','shopt','umask'
def unquote_commands(line):
	kept=[];quote='';index=0;text=str(line or'').replace('\r','\n')
	while index<len(text):
		char=text[index]
		if quote:
			if char=='\\'and quote=='"':index+=2;continue
			if quote=='"':
				if text.startswith('$(',index):kept.append(' $(')
				elif char=='`':kept.append(' `')
			if char==quote:quote=''
			index+=1;continue
		if char in("'",'"'):quote=char;index+=1;continue
		if char=='\\'and index+1<len(text):kept.append(text[index+1]);index+=2;continue
		kept.append(char);index+=1
	return''.join(kept)
INLINE_ACK_RE=re.compile('(?:arena-preview|preview\\.py)\\s+ack(?:\\s+[\\w.-]+)*\\s+"')
def bare_backtick(text):
	index=0
	while True:
		index=text.find('`',index)
		if index<0:return False
		slashes=0
		while index-slashes-1>=0 and text[index-slashes-1]=='\\':slashes+=1
		if slashes%2==0:return True
		index+=1
def tick_warning(line):
	found=INLINE_ACK_RE.search(line or'')
	if not found or not bare_backtick(line[found.end():]):return None
	return'arena-preview gate: that ack text sits in double quotes, so the shell runs each unescaped backtick as a command and the ticks vanish. Single-quote the text.'
SERVE_PORT_RE=re.compile('(?:arena-preview|preview\\.py)\\s+serve\\b[^\\n]*?(?:--port|-p)(?:=|\\s*)(\\d+)')
def serve_warning(line):
	found=SERVE_PORT_RE.search(line or'')
	if not found or int(found.group(1))==DEFAULT_PORT:return None
	return f"arena-preview gate: that serve names port {found.group(1)}, not the standard {DEFAULT_PORT}. The page the owner has open follows the standard address, and the poll follows the recorded port. Name another only when {DEFAULT_PORT} is taken, and say so."
EGRESS_ALLOWLIST=frozenset(('github.com','api.github.com','codeload.github.com','registry.npmjs.org','pypi.org','files.pythonhosted.org','dns.google'))
FETCH_COMMAND_RE=re.compile('(?:^|[\\s;&|(])(?:curl|wget|git\\s+(?:clone|fetch|ls-remote|pull|push))\\b')
URL_HOST_RE=re.compile('(?:https?|ftp)://([^/\\s\\"\'@?]+)')
LOOPBACK_HOSTS=frozenset(('127.0.0.1','localhost','::1'))
def url_host(authority):
	if authority.startswith('['):return authority.split(']',1)[0].lstrip('[').lower()
	return authority.split(':',1)[0].lower()
def egress_warning(line):
	text=line or''
	if not FETCH_COMMAND_RE.search(text):return None
	hosts=[]
	for found in URL_HOST_RE.finditer(text):
		host=url_host(found.group(1))
		if host and host not in EGRESS_ALLOWLIST and host not in LOOPBACK_HOSTS and host not in hosts:hosts.append(host)
	if not hosts:return None
	names=', '.join(hosts);return f"arena-preview gate: {names} sits outside this sandbox's egress allowlist. The TLS handshake fails with SSL_ERROR_SYSCALL, which reads as a transient fault and never clears on a retry. Read it through the fetch_page tool instead."
def first_blocking_piece(line):
	if platform_line(line or''):return None
	for piece in command_pieces(agent_line(line)):
		tokens,index=piece_tokens(piece)
		if index>=len(tokens):continue
		head=tokens[index].rsplit('/',1)[-1].strip('"\'')
		if head in INERT_COMMANDS:continue
		if head.startswith('python')and index+1<len(tokens)and tokens[index+1].rsplit('/',1)[-1].strip('"\'')in('arena-preview','preview.py'):index+=1;head=tokens[index].rsplit('/',1)[-1].strip('"\'')
		rest=tokens[index+1:]
		if head in('arena-preview','preview.py'):
			if any(token.startswith('$(')for token in rest):return piece
			if rest[:1]not in(['task'],['report'])and any('`'in token for token in rest):return piece
			continue
		if'--help'in rest or'-h'in rest:
			if any(token.startswith('$(')or'`'in token for token in rest):return piece
			continue
		return piece
	return None
SHELL_KEYWORDS='do','then','else','elif','if','for','until','while','time'
def command_pieces(line):
	text=unquote_commands(line)
	for separator in('&&','||',';','|','\n'):text=text.replace(separator,'\x00')
	return[piece.strip()for piece in text.split('\x00')if piece.strip()]
def piece_tokens(piece,keywords=False):
	tokens=[token for token in piece.split()if token];index=0
	while index<len(tokens)and'='in tokens[index]and not tokens[index].startswith('-'):index+=1
	if keywords:
		while index<len(tokens)and tokens[index].strip('"\'')in SHELL_KEYWORDS:index+=1
	if index<len(tokens)and tokens[index].rsplit('/',1)[-1].strip('"\'')in SHELL_BASENAMES:
		index+=1
		while index<len(tokens)and tokens[index].startswith('-'):index+=1
	return tokens,index
def piece_head(piece):
	tokens,index=piece_tokens(piece,keywords=True)
	if index>=len(tokens):return''
	return tokens[index].rsplit('/',1)[-1].strip('"\'')
SLEEP_IDLE_CAP_SECONDS=60
def max_sleep_seconds(pieces):
	multipliers={'s':1.,'m':6e1,'h':36e2,'d':864e2};longest=.0
	for piece in pieces:
		tokens,index=piece_tokens(piece,keywords=True)
		if index>=len(tokens):continue
		if tokens[index].rsplit('/',1)[-1].strip('"\'')!='sleep':continue
		total=.0
		for arg in tokens[index+1:]:
			clean=arg.strip('"\'')
			if not clean or clean.startswith('-'):continue
			unit=clean[-1].lower();scale=multipliers.get(unit,1.);number_text=clean[:-1]if unit in multipliers else clean
			try:value=float(number_text)
			except ValueError:continue
			if value>0:total+=value*scale
		longest=max(longest,total)
	return longest
def idle_wait(line,state_dir=None):
	pieces=command_pieces(agent_line(line));heads=[head for head in map(piece_head,pieces)if head]
	if'sleep'not in heads:return None
	if any(head in('arena-preview','preview.py')for head in heads):return'WAIT BLOCKED: a sleep beside a preview call holds the turn. Take the next queued task or read the run later. A routed run keeps its verdict. `arena-preview poll` waits when the queue is empty.'
	if any(token.strip('"\'').endswith('.log')for piece in pieces for token in piece.split()):return'SLEEP BLOCKED: a sleep beside a .log read holds the turn. Wait with `arena-preview poll`, and read runs with `arena-preview logs <name>`.'
	if state_dir:
		running=live_runs(state_dir)
		if running:names=', '.join(running);return f"SLEEP BLOCKED: background run ({names}) is live. Never sleep during a background gate. Delegate queued tasks to idle board workers, use a worktree (`arena-preview worktree start`), or run `arena-preview poll` when only the blocked task remains."
	if max_sleep_seconds(pieces)>=SLEEP_IDLE_CAP_SECONDS:return'SLEEP BLOCKED: a long sleep holds the turn instead of working or polling. Run long commands with `arena-preview run start`, and wait with `arena-preview poll` when no unblocked tasks remain.'
	return None
def quiet_inbox_line(line):return first_blocking_piece(line)is None
EXEMPT_BASH_WORDS='preview','profile','bashrc','arena-state','git commit','--help'
EXEMPT_BASH_PREFIXES='mkdir','[','gh','sleep','true',':','test','git status','git diff','git add'
def inert_bash_command(command):return any(word in command for word in EXEMPT_BASH_WORDS)or command.startswith(EXEMPT_BASH_PREFIXES)
def exempted_bash_line(line):
	raw=line or''
	if not raw.strip()or platform_line(raw):return False
	command=agent_line(raw).strip()
	if not command or platform_line(command)or'git push'in command:return False
	return inert_bash_command(command)or quiet_inbox_line(raw)
SLOW_BASH_SECONDS=10
def slow_bash_hint(line,elapsed_seconds):
	if elapsed_seconds is None or elapsed_seconds<SLOW_BASH_SECONDS:return None
	raw=line or''
	if not raw.strip()or platform_line(raw)or exempted_bash_line(raw):return None
	command=agent_line(raw).strip()
	if not command or any(word in command for word in('arena-preview poll','preview.py poll','preview.py --poll','arena-preview run','preview.py run')):return None
	return f"Bash call took {elapsed_seconds:.1f}s (>= {SLOW_BASH_SECONDS}s). Use `arena-preview run start -t <task-id> <name> -- <cmd>` for slow commands."
BASH_RUNNING_META='bash_running_at'
def refresh_bash_timer(state_dir):
	if(Path(state_dir).resolve()/'state.sqlite3').is_file():timer_store=Store(state_dir,create=False);timer_store.set_meta(AGENT_CALL_ENDED_META,now());timer_store.clear_meta(BASH_RUNNING_META)
def read_set_actions(line):
	actions=[]
	for piece in command_pieces(agent_line(line)):
		tokens=[token for token in unquote_commands(piece).split()if token];index=0
		while index<len(tokens)and'='in tokens[index]and not tokens[index].startswith('-'):index+=1
		while index<len(tokens)and tokens[index].strip('"\'')in SHELL_KEYWORDS:index+=1
		if index>=len(tokens):continue
		head=tokens[index].rsplit('/',1)[-1].strip('"\'')
		if head in INERT_COMMANDS:continue
		if head.startswith('python')and index+1<len(tokens)and tokens[index+1].rsplit('/',1)[-1].strip('"\'')=='preview.py':index+=1;head='preview.py'
		if head not in('arena-preview','preview.py')or index+1>=len(tokens):return None
		actions.append(tokens[index+1].strip('"\''))
	return tuple(actions)
def inbox_pipeline_warning(line):
	cleaned=re.sub('\\d*>&\\d+|\\d*>&-|&>',' ',agent_line(line));lexer=shlex.shlex(cleaned,posix=False,punctuation_chars=';&|()\n');lexer.whitespace=' \t\r';lexer.whitespace_split=True
	try:tokens=list(lexer)
	except ValueError:return None
	piece=[]
	for token in[*tokens,';']:
		if token not in(';','&&','||','|','|&','&','(',')','\n'):piece.append(token);continue
		try:words=[''.join(shlex.split(word))for word in piece]
		except ValueError:return None
		piece=[]
		while words and('='in words[0]or words[0]in SHELL_KEYWORDS):words.pop(0)
		while words and words[0].rsplit('/',1)[-1]in PR_CHECK_WRAPPERS:
			words.pop(0)
			while words and(words[0].startswith('-')or'='in words[0]or words[0].isdigit()):words.pop(0)
		head=words.pop(0).rsplit('/',1)[-1]if words else''
		if head in SHELL_BASENAMES:
			for(index,word)in enumerate(words[:-1]):
				if word.startswith('-')and'c'in word:
					warning=inbox_pipeline_warning(words[index+1])
					if warning:return warning
					break
		if head in('arena-preview','preview.py')and words[:1]in(['read'],['poll'])and token in('|','|&'):return'INBOX BLOCKED: read and poll need full output. Run without a pipe.'
	return None
def read_set_input_warning(line,root=None,cwd=None):
	kept=[];delimiters=[]
	for row in agent_line(line).splitlines():
		if delimiters:
			marker,tabs=delimiters[0]
			if(row.lstrip('\t')if tabs else row)==marker:delimiters.pop(0)
			continue
		kept.append(row)
		for(tabs,single,double,bare)in re.findall('<<(-?)\\s*(?:\'([^\']+)\'|\\"([^\\"]+)\\"|([A-Za-z_]\\w*))',row):delimiters.append((single or double or bare,bool(tabs)))
	text='\n'.join(kept)
	if not re.search('\\b(?:grep|head|tail)\\b',text):return None
	lexer=shlex.shlex(text,posix=False,punctuation_chars=';&|()<>\n');lexer.whitespace=' \t\r';lexer.whitespace_split=True
	try:tokens=list(lexer)
	except ValueError:return None
	base=Path(root or resolve_repo_root()).resolve();here=Path(cwd or Path.cwd())
	for token in tokens:
		if token.startswith("'"):continue
		for(substitution,backtick)in re.findall('(?<!\\\\)\\$\\(([^()]*)\\)|(?<!\\\\)`([^`]*)`',token):
			warning=read_set_input_warning(substitution or backtick,base,here)
			if warning:return warning
	protected=None;incoming=set();piece=[]
	def paths(words):
		nonlocal protected
		if protected is None:skill=base/'.agents'/'skills'/'arena-skill'/'SKILL.md';candidates=[*(base/rel for rel in read_set_files(base)),*([skill]if skill.is_file()else[])];protected={path.resolve():path.relative_to(base).as_posix()for path in candidates if path.is_file()}
		found=set()
		for word in words:
			candidate=(here/Path(word).expanduser()).resolve()
			if candidate in protected:found.add(candidate)
		return found
	for token in[*tokens,';']:
		if token not in(';','&&','||','|','|&','&','(',')','\n'):piece.append(token);continue
		try:words=[''.join(shlex.split(word))for word in piece]
		except ValueError:return None
		piece=[]
		while words and('='in words[0]or words[0]in SHELL_KEYWORDS):words.pop(0)
		while words and words[0].rsplit('/',1)[-1]in PR_CHECK_WRAPPERS:
			wrapper=words.pop(0).rsplit('/',1)[-1]
			if wrapper=='command'and any(word in('-v','-V')for word in words[:1]):words=[];break
			while words and(words[0].startswith('-')or'='in words[0]or words[0].isdigit()):words.pop(0)
		head=words.pop(0).rsplit('/',1)[-1]if words else'';sources=incoming
		if head=='cd'and words:here=(here/Path(words[-1]).expanduser()).resolve()
		elif head in SHELL_BASENAMES:
			for(index,word)in enumerate(words[:-1]):
				if word.startswith('-')and'c'in word:
					warning=read_set_input_warning(words[index+1],base,here)
					if warning:return warning
					break
		elif head in('cat','grep','head','tail'):
			files=[];pattern=head!='grep';flags=True;index=0
			while index<len(words):
				word=words[index];index+=1
				if word in('<','>','>>','<<','<<-','<<<','<&','>&'):
					if word=='<':files.extend(words[index:index+1])
					index+=1
				elif flags and word=='--':flags=False
				elif flags and word.startswith('-'):
					if head=='grep':
						if word in('-e','--regexp','-f','--file'):
							if word in('-f','--file'):files.extend(words[index:index+1])
							pattern=True;index+=1
						elif word in('-m','--max-count','-A','--after-context','-B','--before-context','-C','--context','--exclude','--exclude-dir','--exclude-from','--include','--label','--devices','--directories','--binary-files'):index+=1
						elif word.startswith(('-e','--regexp=','-f','--file=')):
							if word.startswith('-f'):files.append(word[2:])
							elif word.startswith('--file='):files.append(word.split('=',1)[1])
							pattern=True
					elif word in('-n','-c','--lines','--bytes'):index+=1
				elif not pattern:pattern=True
				else:files.append(word)
			sources=paths(files)|incoming
			if head in GATE_NOISE and sources:name=protected[min(sources)];return f"BIBLE BLOCKED: `{head}` reads {name}. Use read_file for the full file."
		incoming=sources if token in('|','|&')else set()
	return None
def read_set_tick(store,line,moment=None,root=None):
	actions=read_set_actions(line)
	if actions and set(actions)=={'poll'}or poll_timeout_line(line):return
	if store.pending_read_set_note()is None and not store.polling():store.schedule_read_set_note(moment=moment,root=root)
GATE_NOISE='tail','grep','head'
RUN_NAME=re.compile('[A-Za-z0-9._-]{1,64}')
RUN_TAIL_LINES=40
RUN_FOOTER_RE=re.compile('^Refreshing checks status every \\d+ seconds\\. Press Ctrl\\+C to quit\\.$')
def clean_run_tail(lines):
	kept=[]
	for line in lines:
		if RUN_FOOTER_RE.match(line):continue
		if line in kept:kept.remove(line)
		kept.append(line)
	return kept
def run_record_path(state_dir,name):return Path(state_dir)/'runs'/f"{name}.json"
def read_marker_path(state_dir,name):return Path(state_dir)/'runs'/f"{name}.read"
def run_read_stamp(state_dir,name):
	try:return read_marker_path(state_dir,name).read_text(encoding='utf-8').strip()
	except OSError:return None
def mark_run_read(state_dir,name,record):
	marker=read_marker_path(state_dir,name)
	try:marker.parent.mkdir(parents=True,exist_ok=True);marker.write_text(str(record.get('started_at')or''),encoding='utf-8')
	except OSError:pass
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
def run_progress_path(state_dir,name):return Path(state_dir)/'runs'/f"{name}.progress.json"
def run_progress_lines(state_dir,name):
	try:lines=json.loads(run_progress_path(state_dir,name).read_text(encoding='utf-8'))
	except(OSError,ValueError):return[]
	if not isinstance(lines,list):return[]
	return[line for line in lines if isinstance(line,str)]
def write_run_progress(state_dir,name,line):write_run_record(run_progress_path(state_dir,name),[*run_progress_lines(state_dir,name),line])
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
	if record is None:raise ValueError(f"No routed run named {name}.")
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
def prune_stale_worktrees(state_dir):
	directory=Path(state_dir)/WORKTREE_DIR
	if not directory.is_dir():return
	for path in sorted(directory.glob('*.json')):
		record=json_record(path);target=record.get('worktree')if record else None
		if not isinstance(target,str)or not Path(target).exists():path.unlink(missing_ok=True)
def forget_worktree(state_dir,name):
	name=checked_worktree_name(name);path=worktree_record_path(state_dir,name)
	if not path.exists():raise ValueError(f"No worktree named {name}.")
	path.unlink(missing_ok=True);return name
def stored_worktree(state_dir,name):
	name=checked_worktree_name(name);record=json_record(worktree_record_path(state_dir,name))
	if record is None:raise ValueError(f"No worktree named {name}.")
	return record
def worktree_at(state_dir,cwd=None):
	prune_stale_worktrees(state_dir);current=Path(cwd or Path.cwd()).resolve();directory=Path(state_dir)/WORKTREE_DIR
	if not directory.is_dir():return None
	for path in sorted(directory.glob('*.json')):
		record=json_record(path);target=record.get('worktree')if record else None
		if isinstance(target,str)and under_path(current,target):return record
	return None
def worktree_warning(state_dir,action,cwd=None):
	record=worktree_at(state_dir,cwd)
	if record is None:return None
	label=WORKTREE_ACTIONS.get(action,action);root=record.get('root');worktree=record.get('worktree');hint=f' Run it from {root} (or `bash -c "cd {worktree} && <command>"` from {root}).'if action=='gate'and worktree else f" Run it from {root}.";return f"WORKTREE BLOCKED: {record.get('name')} is detached. Do not run {label} here.{hint}"
def checked_worktree_name(name):
	if not RUN_NAME.fullmatch(name or''):raise ValueError('Worktree names take 1 to 64 letters, digits, dots, dashes or underscores.')
	return name
def start_worktree(state_dir,name,destination,gate,allowed,cwd=None):
	name=checked_worktree_name(name);prune_stale_worktrees(state_dir)
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
	if gate is not None:
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
	gate_name=record.get('gate')
	if gate_name is not None:
		gate=routed_record(state_dir,gate_name)
		if gate.get('exit')is None:raise ValueError(f"Gate {gate_name} is still running.")
		if gate.get('exit')!=0:raise ValueError(f"Gate {gate_name} did not pass.")
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
RUN_CHAIN_POLL_SECONDS=.1
def run_chain_error(state_dir,record):
	dependency=record.get('after')
	if dependency is None:return None
	if not isinstance(dependency,dict)or not isinstance(dependency.get('name'),str):return'CANCELLED: invalid gate dependency.'
	while True:
		try:parent=routed_record(state_dir,dependency['name'])
		except ValueError as error:return f"CANCELLED: {error}"
		if parent.get('run_id')!=dependency.get('run_id'):return f"CANCELLED: gate {dependency['name']} has another run instance."
		if parent.get('exit')is not None:
			if parent['exit']!=0:return f"CANCELLED: gate {dependency['name']} failed with exit {parent['exit']}."
			return None
		time.sleep(RUN_CHAIN_POLL_SECONDS)
PYTEST_FAILURE_HEADER_RE=re.compile('^_{3,}\\s+(.+?)\\s+_{3,}$')
PYTEST_SECTION_RE=re.compile('^={3,}\\s+(.+?)\\s+={3,}$')
def pytest_failure_tracebacks(lines):
	in_failures=False;blocks={};order=[];current_name=None;current_lines=[]
	def flush():
		nonlocal current_name,current_lines
		if current_name and current_lines:
			if current_name not in blocks:order.append(current_name)
			blocks[current_name]=list(current_lines[-10:])
		current_name=None;current_lines=[]
	for line in lines:
		section=PYTEST_SECTION_RE.match(line.strip())
		if section:flush();in_failures=section.group(1).strip().upper()=='FAILURES';continue
		if not in_failures:continue
		header=PYTEST_FAILURE_HEADER_RE.match(line.strip())
		if header:flush();current_name=header.group(1).strip();current_lines=[line]
		elif current_name is not None:
			if line.strip():current_lines.append(line)
		elif line.strip():
			key=line.split(':',2)[0]
			if key not in blocks:order.append(key)
			blocks[key]=[line]
	flush();retained=[]
	for name in order:retained.extend(blocks[name])
	return retained
def run_command(state_dir,name,argv):
	if not RUN_NAME.fullmatch(name or''):print('arena-preview run start: the name takes 1 to 64 letters, digits, dot, dash or underscore.',flush=True);return 2
	if argv[:1]==['--']:argv=argv[1:]
	if not argv:print('arena-preview run start: name a command after --, for example: run start gates check',flush=True);return 2
	started=time.monotonic();dependency_error=run_chain_error(state_dir,json_record(run_record_path(state_dir,name))or{})
	if dependency_error:completed=subprocess.CompletedProcess(argv,1,dependency_error+'\n','')
	elif pr_checks_command(argv):
		root=resolve_repo_root();status,watched=pr_checks_watch(root,progress=lambda line:write_run_progress(state_dir,name,line))
		if status=='failure':watched+=[f"Failed: {step}"for step in pr_failed_steps(root)]
		completed=subprocess.CompletedProcess(argv,0 if status=='success'else 1,'\n'.join(watched)+'\n','')
	else:completed=subprocess.run(argv,capture_output=True,text=True,check=False)
	seconds=round(time.monotonic()-started,1);output=(completed.stdout+completed.stderr).splitlines();tail=clean_run_tail(output[-RUN_TAIL_LINES:]);tracebacks=pytest_failure_tracebacks(output)
	if tracebacks:
		missing=[line for line in tracebacks if line not in tail]
		if missing:tail=missing+tail
	verdict=tail[-1]if tail else'(no output)';path=run_record_path(state_dir,name);existing=json_record(path)or{};record={**existing,'run_id':existing.get('run_id')or uuid.uuid4().hex,'name':name,'argv':argv,'exit':completed.returncode,'seconds':seconds,'finished_at':now(),'verdict':verdict,'tail':tail,'announced':False};write_run_record(path,record)
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
def live_task_runs(state_dir):
	linked={};runs=Path(state_dir)/'runs'
	if not runs.is_dir():return linked
	for path in sorted(runs.glob('*.json')):
		record=json_record(path)
		if record is None:continue
		record=reconcile_routed_record(path,record);task_id=record.get('task_id')
		if record.get('detached')and record.get('exit')is None and isinstance(task_id,str)and task_id:linked.setdefault(task_id,[]).append(record.get('name')or path.stem)
	return linked
def remote_watch_run(state_dir,name):
	if re.match('^(?:pr-checks|run-watch)(?:-\\d+)?$',str(name)):return True
	record=json_record(run_record_path(state_dir,name))or{};argv=record.get('argv')or[]
	if pr_checks_command(argv):return True
	text=' '.join(str(item)for item in argv);return long_run_name(text)in{'pr-checks','run-watch'}
def link_task_runs(records,state_dir):
	linked=live_task_runs(state_dir)
	for record in records:
		names=linked.get(record.get('id'))
		if names:
			record['runs']=names
			if record.get('status')!='finished':record['blocked']=True
		else:record.pop('runs',None)
	return records
def guard_task_runs(state_dir,task_id=None,action='change'):
	linked=live_task_runs(state_dir);target_map=linked if task_id is None else{task_id:linked.get(task_id,[])};held=[]
	for(identifier,names)in target_map.items():
		blocking=[name for name in names if not remote_watch_run(state_dir,name)]if action=='finish task'else list(names)
		if blocking:held.append((identifier,blocking))
	if not held:return
	labels='; '.join(f"{identifier}: {', '.join(names)}"for(identifier,names)in sorted(held));raise ValueError(f"Cannot {action}; live task run(s): {labels}")
PUSH_GUARDED_TASK_TYPES='code','docs'
def is_private_repo(repo_root,store=None):
	if store is not None:
		try:cached=store.meta_value(REPO_PRIVATE_META)
		except Exception:cached=None
		if cached in('0','1'):return cached=='1'
	if repo_root is None:return False
	root=Path(repo_root)
	if not(root/'.git').exists():return False
	origin_proc=subprocess.run(['git','config','--get','remote.origin.url'],cwd=root,capture_output=True,text=True,check=False);origin_url=origin_proc.stdout.strip()if origin_proc.returncode==0 else''
	if'github.com'not in origin_url:return False
	try:gh_proc=subprocess.run(['gh','repo','view','--json','isPrivate','--jq','.isPrivate'],cwd=root,capture_output=True,text=True,timeout=3,check=False)
	except(OSError,subprocess.SubprocessError):return False
	verdict=gh_proc.stdout.strip().lower()if gh_proc.returncode==0 else''
	if verdict not in('true','false'):return False
	private=verdict=='true'
	if store is not None:
		try:store.set_meta(REPO_PRIVATE_META,'1'if private else'0')
		except Exception:pass
	return private
def unpushed_commit_count(repo_root):
	if repo_root is None:return 0
	root=Path(repo_root)
	if not(root/'.git').exists():return 0
	branch_proc=subprocess.run(['git','rev-parse','--abbrev-ref','HEAD'],cwd=root,capture_output=True,text=True,check=False);branch=branch_proc.stdout.strip()if branch_proc.returncode==0 else''
	if not branch or branch=='HEAD':return 0
	for target in(f"refs/remotes/origin/{branch}",'@{upstream}'):
		count_proc=subprocess.run(['git','rev-list','--count',f"{target}..HEAD"],cwd=root,capture_output=True,text=True,check=False)
		if count_proc.returncode==0:
			try:return max(0,int(count_proc.stdout.strip()))
			except ValueError:return 0
	return 0
def guard_task_push(repo_root,task_id,task_type):
	if task_type not in PUSH_GUARDED_TASK_TYPES or repo_root is None:return
	root=Path(repo_root)
	if not(root/'.git').exists():return
	dirty=subprocess.run(['git','status','--porcelain','-uno'],cwd=root,capture_output=True,text=True,check=False)
	if dirty.returncode==0 and dirty.stdout.strip():raise ValueError(f"FINISH BLOCKED: commit and push verified changes before finishing {task_id}.")
	branch_proc=subprocess.run(['git','rev-parse','--abbrev-ref','HEAD'],cwd=root,capture_output=True,text=True,check=False);branch=branch_proc.stdout.strip()if branch_proc.returncode==0 else''
	if not branch or branch=='HEAD':return
	remote_proc=subprocess.run(['git','rev-parse','--verify',f"refs/remotes/origin/{branch}"],cwd=root,capture_output=True,text=True,check=False);head_proc=subprocess.run(['git','rev-parse','--verify','HEAD'],cwd=root,capture_output=True,text=True,check=False)
	if remote_proc.returncode==0 and head_proc.returncode==0 and remote_proc.stdout.strip()!=head_proc.stdout.strip():
		if is_private_repo(root)and unpushed_commit_count(root)<PRIVATE_BATCH_CAP:return
		raise ValueError(f"FINISH BLOCKED: commit and push verified changes before finishing {task_id}.")
def skip_poll_ready(store):return store.skip_poll_requested()and not live_runs(store.path.parent)
def run_notices(state_dir,show_running=False,out=None):
	out=sys.stdout if out is None else out;runs=Path(state_dir)/'runs'
	if not runs.is_dir():return 0
	records=[]
	for path in sorted(runs.glob('*.json')):
		record=json_record(path)
		if record is not None:records.append((path,reconcile_routed_record(path,record)))
	records.sort(key=lambda item:(not remote_watch_run(state_dir,item[1].get('name')or item[0].stem),item[0].name))
	for(path,record)in records:
		if record.get('exit')is None or record.get('announced'):continue
		print(f"run {record.get('name')} done: exit {record.get('exit')} {record.get('seconds')}s {record.get('verdict')}",file=out,flush=True);record['announced']=True;write_run_record(path,record);return 1
	for(path,record)in records:
		if record.get('exit')is not None or not record.get('detached'):continue
		name=record.get('name')or path.stem;progress=run_progress_lines(state_dir,name);announced=record.get('progress_announced')
		if not isinstance(announced,int)or isinstance(announced,bool)or announced<0:announced=0
		if announced>=len(progress):continue
		print(f"run {name} progress: {progress[announced]}",file=sys.stderr,flush=True);record['progress_announced']=announced+1;write_run_record(path,record);return 1
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
PR_CHECK_WRAPPERS='env','command','timeout','xargs','nice','nohup','stdbuf','sudo'
PR_CHECK_NAMES=('pr','checks','pr-checks'),('run','watch','run-watch')
LONG_RUN_HEADS={'pytest':'pytest','py.test':'pytest','tox':'tox','nox':'nox'}
LONG_RUN_WORDS=(('npm','test'),'npm-tests'),(('npm','run','test*'),'npm-tests'),(('yarn','test'),'yarn-tests'),(('pnpm','test'),'pnpm-tests'),(('make','test'),'make-tests'),(('npx','jest'),'npx-tests'),(('npx','vitest'),'npx-tests'),(('npx','mocha'),'npx-tests'),(('npx','ava'),'npx-tests'),(('go','test'),'go-tests'),(('cargo','test'),'cargo-tests'),(('dotnet','test'),'dotnet-tests'),(('mvn','test'),'mvn-tests'),(('mvnw','test'),'mvn-tests'),(('gradle','test'),'gradle-tests'),(('gradlew','test'),'gradle-tests')
WORKTREE_STEP='NEXT: delegate independent queued tasks to idle board workers first, else work in worktree while this runs: `arena-preview worktree start [-g, --gate <gate-name>] [-a, --allow <glob>] <name> <destination>`.'
DETACHED_NOTICE='RUN DETACHED: `{name}` runs in background, call ends clean. Verdict on later call. '+WORKTREE_STEP
DETACHED_BLOCK='RUN BLOCKED: background start failed, so nothing ran. Use `arena-preview run start {name} -- <the same line>` instead.'
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
		if head in INERT_COMMANDS or head in SHELL_BASENAMES:continue
		if any(flag in tokens[index+1:]for flag in('--help','-h','--version','-V')):continue
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
def pr_checks_command(argv):line=argv[-1]if len(argv)>2 and argv[0]in SHELL_BASENAMES else' '.join(argv);return long_run_name(line)=='pr-checks'
JOB_LOG_PATH_RE=re.compile('(?:^|/|repos/[^/\\s\\"\']+/[^/\\s\\"\']+/)actions/(jobs|runs)/(\\d+)/logs\\b')
PR_PATCH_PATH_RE=re.compile('(?:^|/|repos/[^/\\s\\"\']+/[^/\\s\\"\']+/)pulls/(\\d+)\\b')
def pr_session_id_hits(store,title,body,body_label='body'):
	if store is None:return[]
	tokens=session_id_tokens(store)
	if not tokens:return[]
	patterns={token:re.compile(f"(?<![0-9A-Za-z]){re.escape(token)}(?![0-9A-Za-z])")for token in tokens};hits=[]
	for(number,line)in enumerate(str(title or'').splitlines(),1):
		for(token,pattern)in patterns.items():
			if pattern.search(line):entry_type,ident=tokens[token];hits.append(('title',number,token,entry_type,ident))
	for(number,line)in enumerate(str(body or'').splitlines(),1):
		for(token,pattern)in patterns.items():
			if pattern.search(line):entry_type,ident=tokens[token];hits.append((body_label,number,token,entry_type,ident))
	return hits
def pr_update_command(number,title,body_file,root=None,store=None):
	root_path=Path(root or resolve_repo_root()).resolve()
	if type(number)is not int or number<=0:raise ValueError('Pull request number must be a positive integer')
	clean_title=str(title or'').strip()
	if not clean_title:raise ValueError('Pull request title must not be empty')
	raw_arg=str(body_file or'').strip().removeprefix('@')
	if not raw_arg:raise ValueError('Pull request body file is required')
	raw_path=Path(raw_arg);candidate=raw_path.resolve()if raw_path.is_absolute()else(Path.cwd()/raw_path).resolve()
	if not candidate.is_relative_to(root_path):
		if str(candidate).startswith('/tmp/')or candidate==Path('/tmp'):raise ValueError('Stage pull request text in the workspace, never /tmp')
		raise ValueError(f"Pull request body file must be inside the workspace ({root_path})")
	if not candidate.is_file():raise ValueError(f"Pull request body file not found: {raw_arg}")
	body=candidate.read_text(encoding='utf-8');rel=candidate.relative_to(root_path).as_posix()
	if store is None:
		candidate_state=root_path/'arena-state'if root is not None else Path(resolve_state_dir())
		if state_file(candidate_state).is_file():store=Store(candidate_state,create=False)
	hits=pr_session_id_hits(store,clean_title,body,body_label=rel)
	if hits:
		lines=['PR UPDATE BLOCKED: the pull request title or body cites a session-local ID. Strip message, report, and task IDs before updating the pull request:']
		for(source_label,line_no,token,entry_type,ident)in hits:named=ident if token==ident else f"{token} of {ident}";lines.append(f"  {source_label}:{line_no}: {entry_type} {named}")
		raise ValueError('\n'.join(lines))
	slug=repo_slug(root_path)
	if not slug:raise ValueError('Could not resolve GitHub repository slug from origin remote')
	payload=json.dumps({'title':clean_title,'body':body});patch_proc=subprocess.run(['gh','api',f"repos/{slug}/pulls/{number}",'-X','PATCH','--input','-'],input=payload,cwd=root_path,capture_output=True,text=True,check=False)
	if patch_proc.returncode!=0:detail=(patch_proc.stderr or patch_proc.stdout or'gh api PATCH failed').strip();raise ValueError(f"Pull request #{number} update failed: {detail}")
	fetch_proc=subprocess.run(['gh','api',f"repos/{slug}/pulls/{number}"],cwd=root_path,capture_output=True,text=True,check=False)
	if fetch_proc.returncode!=0:detail=(fetch_proc.stderr or fetch_proc.stdout or'gh api GET failed').strip();raise ValueError(f"Pull request #{number} verification read failed: {detail}")
	try:fetched=json.loads(fetch_proc.stdout)
	except json.JSONDecodeError as error:raise ValueError(f"Pull request #{number} verification returned invalid JSON: {error}")from None
	if fetched.get('title')!=clean_title or(fetched.get('body')or'')!=body:raise ValueError(f"Pull request #{number} title/body parity mismatch after PATCH")
	return f"Updated PR #{number}: {clean_title} (verified against {rel})"
def job_log_command(store=None,job_id=None,run_id=None,root=None):
	job_clean=str(job_id or'').strip();run_clean=str(run_id or'').strip()
	if not job_clean and not run_clean:raise ValueError('Supply --job <job-id> or --run <run-id>')
	if job_clean and not job_clean.isdigit():raise ValueError('--job must be a numeric GitHub Actions job ID')
	if run_clean and not run_clean.isdigit():raise ValueError('--run must be a numeric GitHub Actions run ID')
	root_path=Path(root or resolve_repo_root()).resolve();slug=repo_slug(root_path)
	if not slug:raise ValueError('Could not resolve GitHub repository slug from origin remote')
	target_id=run_clean or job_clean;log_type='runs'if run_clean else'jobs'
	if job_clean:
		proc=subprocess.run(['gh','api','-i',f"/repos/{slug}/actions/jobs/{job_clean}/logs"],cwd=root_path,capture_output=True,text=True,check=False);combined=f"{proc.stdout}\n{proc.stderr}";match=re.search('(?im)^location:\\s*(\\S+)',combined)
		if match:return match.group(1).strip()
	record=store.agent_key()if store is not None else None
	if record and record.get('host')and record.get('key'):host=str(record['host']).rstrip('/');key=record['key'];return f"{host}/v1/gh?key={key}&path=repos/{slug}/actions/{log_type}/{target_id}/logs"
	return f"gh api -i /repos/{slug}/actions/{log_type}/{target_id}/logs"
def _extract_pr_patch_args(text):
	title=None;body_file=None;jq_title=re.search('--arg\\s+title\\s+(?:\\"([^\\"]+)\\"|\'([^\']+)\'|([^\\s\\"\'|]+))',text)
	if jq_title:title=next(group for group in jq_title.groups()if group is not None)
	gh_title=re.search('-(?:f|F|--field|--raw-field)\\s+title=(?:\\"([^\\"]+)\\"|\'([^\']+)\'|([^\\s\\"\'|]+))',text)
	if gh_title and title is None:title=next(group for group in gh_title.groups()if group is not None)
	jq_body=re.search('--rawfile\\s+body\\s+(?:\\"([^\\"]+)\\"|\'([^\']+)\'|([^\\s\\"\'|]+))',text)
	if jq_body:body_file=next(group for group in jq_body.groups()if group is not None)
	gh_body=re.search('-(?:f|F|--field|--raw-field)\\s+body=@(?:\\"([^\\"]+)\\"|\'([^\']+)\'|([^\\s\\"\'|]+))',text)
	if gh_body and body_file is None:body_file=next(group for group in gh_body.groups()if group is not None)
	return title,body_file
def route_recipe_command(state_dir,line,root=None):
	if platform_line(line):return None
	text=agent_line(line or'').strip()
	if not text:return None
	heads=[head for head in map(piece_head,command_pieces(text))if head]
	if not any(head in('gh','curl')for head in heads):return None
	if any(head in('arena-preview','preview.py')for head in heads):return None
	job_match=JOB_LOG_PATH_RE.search(text)
	if job_match:
		log_type,target_id=job_match.groups();flag='-j'if log_type=='jobs'else'-r';print(f"Routed to: arena-preview job-log {flag} {target_id}",file=sys.stderr,flush=True);store=Store(state_dir,create=False)if state_file(state_dir).is_file()else None
		try:url=job_log_command(store,job_id=target_id if log_type=='jobs'else None,run_id=target_id if log_type=='runs'else None,root=root)
		except ValueError as error:print(f"Preview error: {error}",file=sys.stderr,flush=True);return 130
		print(url,flush=True);return 2
	pr_match=PR_PATCH_PATH_RE.search(text)
	if pr_match and re.search('(?:-X|--method)(?:=|\\s+)[\'\\"]?PATCH\\b',text,re.IGNORECASE):
		number=int(pr_match.group(1));title,body_file=_extract_pr_patch_args(text)
		if not title or not body_file:print(f"PR UPDATE ROUTED: use `arena-preview pr-update -n {number} -t <title> -f <workspace-file>` instead of direct gh api/curl PATCH calls.",file=sys.stderr,flush=True);return 130 if'curl'in heads else None
		print(f"Routed to: arena-preview pr-update -n {number} -t {shlex.quote(title)} -f {shlex.quote(body_file)}",file=sys.stderr,flush=True)
		try:receipt=pr_update_command(number,title,body_file,root=root)
		except ValueError as error:print(f"Preview error: {error}",file=sys.stderr,flush=True);return 130
		print(receipt,flush=True);return 2
	return None
def post_push_pr_checks_command(line):
	if platform_line(line):return None
	text=unquote_commands(agent_line(line or''))
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
		if head in INERT_COMMANDS or head in SHELL_BASENAMES:continue
		rest=tokens[index+1:]
		if any(flag in rest for flag in('--help','-h','--version','-V','--dry-run','-n')):continue
		if head=='git':
			pos=0;sub=None
			while pos<len(rest):
				tok=rest[pos]
				if tok in('-C','-c','--git-dir','--work-tree','--namespace'):pos+=2;continue
				if tok.startswith('-'):pos+=1;continue
				sub=tok;break
			if sub=='push':return'pr-checks'
		elif head=='gh':
			words=[tok for tok in rest if not tok.startswith('-')]
			if tuple(words[:2])==('pr','create'):return'pr-checks'
	return None
def auto_start_post_push_pr_checks(state_dir,line,rc=0):
	if rc!=0 or not post_push_pr_checks_command(line):return False
	if'pr-checks'in live_runs(state_dir):return False
	if not detach_run(state_dir,'pr-checks',['bash','-c','gh pr checks --watch'],link_task=False):return False
	print('routed pr-checks: background run after push. Verdict on later call.',file=sys.stderr,flush=True);return True
def detached_seconds(started_at):
	try:
		started=datetime.fromisoformat(str(started_at))
		if started.tzinfo is None:started=started.replace(tzinfo=UTC)
		return max(0,int((datetime.now(UTC)-started).total_seconds()))
	except(TypeError,ValueError):return None
def write_detached_placeholder(state_dir,name,argv,pid=None,pid_started_at=None,task_id=None,after=None):
	path=run_record_path(state_dir,name)
	try:run_progress_path(state_dir,name).unlink()
	except OSError:pass
	record={'run_id':uuid.uuid4().hex,'name':name,'argv':list(argv),'exit':None,'seconds':None,'started_at':now(),'finished_at':None,'verdict':None,'tail':[],'detached':True,'announced':False}
	if after is not None:record['after']=after
	if task_id:record['task_id']=task_id
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
	line=agent_line(line);name=long_run_name(line)
	if not name:return PR_CHECK_OTHER
	warning=worktree_warning(state_dir,'gate')
	if warning:print(warning,file=sys.stderr,flush=True);return PR_CHECK_REFUSED
	if command is not None and line.strip()!=command.strip():print(f"RUN BLOCKED: automatic routing would replay a mixed shell line, so nothing ran. Use `arena-preview run start {name} -- <remaining command>`; do not repeat completed commands.",file=sys.stderr,flush=True);return PR_CHECK_REFUSED
	if not detach_run(state_dir,name,['bash','-c',line]):print(DETACHED_BLOCK.format(name=name),file=sys.stderr,flush=True);return PR_CHECK_REFUSED
	print(DETACHED_NOTICE.format(name=name),file=sys.stderr,flush=True);return PR_CHECK_RUNS
def run_task_id(state_dir,selected=None):
	try:store=Store(state_dir)
	except FileNotFoundError:
		if selected:raise ValueError(f"No task is stored under {selected}")from None
		return None
	if selected:
		try:task_id=store.resolve_id(selected)
		except ValueError as error:raise ValueError(f"Cannot link routed run: {error}")from None
		record=store.task_record(task_id)
		if record is None:raise ValueError(f"No task is stored under {selected}")
		if record.get('status')!='upcoming':raise ValueError(f"Task {task_id} is finished; a run needs an upcoming task")
		return task_id
	record=current_task(store.list_tasks());return record.get('id')if record else None
def detach_run(state_dir,name,argv,task_id=None,after=None,link_task=True):
	path=run_record_path(state_dir,name);standing=json_record(path)
	if standing is not None:
		standing=reconcile_routed_record(path,standing)
		if standing.get('exit')is None:return True
	dependency=None
	if after is not None:
		if after==name:raise ValueError('A gate cannot wait for itself')
		parent=routed_record(state_dir,after)
		if parent.get('exit')is None and not parent.get('detached'):raise ValueError(f"Gate {after} has no running or finished record")
		dependency={'name':after,'run_id':parent.get('run_id')}
	task_id=run_task_id(state_dir,task_id)if link_task else None
	if os.environ.get(DETACH_DRY_RUN):pid=os.getpid();write_detached_placeholder(state_dir,name,argv,pid,process_start_identity(pid),task_id,dependency);return True
	path=write_detached_placeholder(state_dir,name,argv,task_id=task_id,after=dependency);child_env={key:value for(key,value)in os.environ.items()if key not in('BASH_ENV',DETACH_DRY_RUN,'ARENA_PREVIEW_ROUTE')};child_env['ARENA_PREVIEW_ROUTE']='1'
	try:child=subprocess.Popen([sys.executable,str(Path(__file__).resolve()),'route-run',name,'--',*argv],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,env=child_env,start_new_session=True,close_fds=True)
	except OSError:terminal_route_failure(path,json_record(path)or{},ROUTE_START_FAILED);return False
	record=json_record(path)
	if record is not None and record.get('exit')is None:
		record['pid']=child.pid;started=process_start_identity(child.pid)
		if started:record['pid_started_at']=started
		write_run_record(path,record)
	return True
def resolve_run_name(state_dir,name):
	if not run_record_path(state_dir,name).exists():return name
	base=re.sub('-\\d+$','',name)or name;suffix=2
	while True:
		candidate=f"{base[:64-len(str(suffix))-1]}-{suffix}"
		if not run_record_path(state_dir,candidate).exists():return candidate
		suffix+=1
def route_command(state_dir,name,argv,task_id=None,after=None):
	if not RUN_NAME.fullmatch(name or''):print('arena-preview run start: the name takes 1 to 64 letters, digits, dot, dash or underscore.',flush=True);return 2
	if argv[:1]==['--']:argv=argv[1:]
	if not argv:print('arena-preview run start: name a command after --, for example: run start gates check',flush=True);return 2
	try:clear_poll_repeat(Store(state_dir,create=False))
	except FileNotFoundError:pass
	resolved=resolve_run_name(state_dir,name)
	if not detach_run(state_dir,resolved,argv,task_id=task_id,after=after):print(f"arena-preview run start: the start of `{resolved}` failed.",flush=True);return 1
	print(f"routed {resolved}: background run. Verdict on later call. {WORKTREE_STEP}",file=sys.stderr,flush=True);return 0
def next_open_task(state_dir,gate_name):
	try:store=Store(state_dir)
	except FileNotFoundError:return None
	held=str(routed_record(state_dir,gate_name).get('task')or'')
	for task in store.list_tasks():
		if task.get('status')!='upcoming'or task.get('id')==held:continue
		if gate_name in(task.get('runs')or[]):continue
		return task
	return None
def idle_board_workers(state_dir):
	if not state_dir or not(Path(state_dir).resolve()/'state.sqlite3').is_file():return[]
	try:board=Store(state_dir).board_state()
	except Exception:return[]
	if board.get('board_role')!='main':return[]
	allowed=set(board.get('allowed_sessions')or());worker_tasks=board.get('worker_tasks')or{};idle=[]
	for row in board.get('sessions')or():
		sid=row.get('branch')or''
		if not sid or sid not in allowed or row.get('role')!='worker'or row.get('disconnected'):continue
		active=[t for t in worker_tasks.get(sid)or()if t.get('status')=='upcoming']
		if not active:idle.append(str(row.get('worker_id')or row.get('board_id')or sid))
	return idle
def next_task_hint(state_dir,gate_name):
	task=next_open_task(state_dir,gate_name)
	if task is None:return''
	idle=idle_board_workers(state_dir);worker_prefix=f" Delegate to idle board worker @{idle[0]} via /api/board first, or start"if idle else' Start';return f" Next open task: {task['id']} \"{task.get('title')or''}\".{worker_prefix} it in a worktree: `arena-preview worktree start --gate {gate_name} --allow '<task path glob>' {task['id']} <destination outside the repo root>`."
def run_log_report(state_dir,name):
	record=routed_record(state_dir,name)
	if record.get('exit')is None:
		stamp=str(record.get('started_at')or'')
		if run_read_stamp(state_dir,name)==stamp:print(f"WAIT BLOCKED: run {name} has no verdict yet.{next_task_hint(state_dir,name)} The verdict keeps, and `arena-preview poll` waits when the queue is empty.",file=sys.stderr,flush=True);return 130
		mark_run_read(state_dir,name,record);elapsed=detached_seconds(record.get('started_at'));age=f" {elapsed}s"if elapsed is not None else'';print(f"run {name} running{age}")
		for line in run_progress_lines(state_dir,name):print(line)
		print(WORKTREE_STEP);return 0
	print(f"run {name} done: exit {record['exit']} {record.get('seconds')}s")
	for line in clean_run_tail(record.get('tail')or[]):print(line)
	return 0
def poll_clock():return int(time.time())
def clear_poll_repeat(store):
	if store.meta_value(POLL_REPEAT_META):store.set_meta(POLL_REPEAT_META,0)
def poll_repeat(store):
	moment=poll_clock();previous=store.meta_value(POLL_REPEAT_META);store.set_meta(POLL_REPEAT_META,moment)
	try:recent=previous is not None and 0<=moment-int(previous)<=POLL_REPEAT_SECONDS
	except ValueError:recent=False
	if not recent or store.meta_value(POLL_IMMEDIATE_META)!='1':return None
	store.request_interrupt('tasks',user_flagged=False);return'VIOLATION: repeat poll inside 45 seconds. Read only paths in the bible whose current full text is absent from context, then work the task.'
def poll_once(store,sleeper=None):
	started=time.monotonic();status=poll_inbox(store,sleeper=sleeper)
	if time.monotonic()-started>=POLL_IMMEDIATE_SECONDS:store.clear_meta(POLL_IMMEDIATE_META)
	else:store.set_meta(POLL_IMMEDIATE_META,'1')
	return status
def _notify_server_poll(store,active):
	port=store.meta_value('port')
	if not port:return
	try:
		from urllib.request import Request,urlopen;req=Request(f"http://127.0.0.1:{int(port)}/api/poll",data=json.dumps({'active':bool(active)}).encode('utf-8'),headers={'Content-Type':'application/json'},method='POST')
		with urlopen(req,timeout=.5):pass
	except Exception:pass
def poll_inbox(store,sleeper=None):
	if sleeper is None:sleeper=time.sleep
	deadline=time.monotonic()+POLL_WAIT_SECONDS;listing={'checked_at':None,'pending':[]};print(f"poll max {POLL_WAIT_SECONDS} s, bash cap 1800 s. {REMINDERS[0]}",file=sys.stderr,flush=True);store.start_poll();_notify_server_poll(store,True);failures=[];stop_checks=threading.Event();threading.Thread(target=pr_check_watch,args=(failures,resolve_repo_root(),stop_checks),daemon=True).start();announced_running=False
	try:
		for index in range(POLL_MAX_LOOPS):
			if time.monotonic()>=deadline:break
			violation=store.pending_violation_note();listing=store.read(include_quiet=False)
			if violation or listing['pending']:store.take_board_wake();print_read(store);return 0
			if failures and'pr-checks'not in live_runs(store.path.parent):print(f"PR CHECKS FAILED: {failures[0]}. Fix the failed check before the next push.",flush=True);return 0
			if run_notices(store.path.parent):clear_poll_repeat(store);print('DO NOT END TURN. POLL AGAIN.',flush=True);return 0
			upcoming=[item for item in store.list_tasks()if item['status']=='upcoming'];upcoming_ids={item['id']for item in upcoming};open_tasks=[item for item in upcoming if not item['blocked']and not any(dep in upcoming_ids for dep in item.get('blocked_by')or())]
			if open_tasks:store.take_board_wake();listing['tasks']=open_tasks;names=', '.join(item['id']for item in open_tasks);idle=idle_board_workers(store.path.parent);worker_step=f"Delegate to idle board worker @{idle[0]} via arena-preview board post first, or start"if idle else'Start';continue_line=f"CONTINUE: UNBLOCKED TASK {names}. {worker_step} a worktree with: arena-preview worktree start --gate <gate> <name> <destination>.";print(continue_line,flush=True);print(format_task_list(listing.get('tasks',[])),flush=True);return 0
			board_woken=bool(store.take_board_wake());is_worker_role=store.board_state().get('board_role')=='worker';board_notices=store.take_board_notifications(include_peer_notices=board_woken or not is_worker_role)
			if board_woken or board_notices:
				clear_poll_repeat(store);unseen_entries=store.take_unseen_board_entries();has_work=bool(unseen_entries or listing.get('pending'));wake_head='BOARD WAKE: session woken from the multi-session board. Read board entries and continue work before polling again.'if has_work else'BOARD WAKE: session woken from the multi-session board. DO NOT END TURN. Delegate queued work if main, else run arena-preview poll again.';print(wake_head,flush=True)
				for notice in board_notices:print(notice,flush=True)
				pending_board_ids={str(item.get('id')or'')for item in listing.get('pending')or[]if isinstance(item,dict)and item.get('type')=='board'};repo_root=store.repository_root()
				for entry in unseen_entries:
					payload=entry.get('payload');body_text=json.dumps(payload,ensure_ascii=False)if isinstance(payload,dict)else str(payload or'');file_obj=entry.get('file')if isinstance(entry.get('file'),dict)else None;file_path=file_obj.get('staged_path')or file_obj.get('name')or''if file_obj else'';file_suffix=f" [file: {file_path}]"if file_path else'';head=f"[board {entry.get('id','')}] {entry.get('branch','')} -> {entry.get('to','*')}";print(f"{head}: {body_text}{file_suffix}",flush=True);patch_hint=board_patch_apply_hint(entry,state_dir=store.path.parent,repo_root=repo_root)
					if patch_hint and str(entry.get('id')or'')not in pending_board_ids:print(patch_hint,flush=True)
				print(format_read(listing),flush=True);return 0
			if skip_poll_ready(store):store.take_skip_poll();store.mark_turn_ended();print('SKIP: owner pressed Skip poll. End the turn, no second poll.',flush=True);print(format_read(listing),flush=True);return 0
			if not announced_running:announced_running=True;run_notices(store.path.parent,show_running=True)
			if index+1<POLL_MAX_LOOPS:
				remaining=deadline-time.monotonic()
				if remaining<=0:break
				sleeper(min(POLL_INTERVAL,remaining));store.stamp_polling()
	finally:stop_checks.set();store.clear_polling();_notify_server_poll(store,False)
	store.mark_turn_ended();print('No new messages. End turn.',flush=True);return 1
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
AUTOSAVE_HISTORY='userscript-autosave-history'
AUTOSAVE_RECEIPTS='userscript-autosave-receipts'
AUTOSAVE_OFFER_META='userscript-autosave-offered'
FORCE_SAVE_META='userscript-force-save'
BOARD_STATE_META='board-state'
CLEARED_META='state-explicitly-cleared'
CLEARED_AT_META='state-cleared-at'
BACKUP_DIR='backups'
LATEST_BACKUP='state-latest.ndjson'
PREVIOUS_BACKUP='state-previous.ndjson'
def board_branch(branch_or_root=None):
	if isinstance(branch_or_root,Path)or branch_or_root is None:raw=workspace_branch(branch_or_root or Path.cwd())
	else:raw=str(branch_or_root)
	clean=raw.strip()
	if not clean or'..'in clean or'\\'in clean or'//'in clean or clean.startswith('/')or clean.endswith('/')or any(ch.isspace()for ch in clean):return''
	return clean[:128]
def encode_board_branch_dir(branch):clean=board_branch(branch)or'session';return quote(clean,safe='A-Za-z0-9._-')
def branch_slug_tail(branch):clean=board_branch(branch);return clean.rsplit('/',1)[-1]if clean else''
def stage_board_file(state_dir,sender_branch,file_obj):
	if not isinstance(file_obj,dict):raise TypeError('Board file must be an object')
	raw_name=str(file_obj.get('name')or'').strip().replace('\\','/');parts=[part for part in raw_name.split('/')if part and part!='.']
	if not parts or any(part=='..'for part in parts)or raw_name.startswith('/'):raise ValueError('Invalid board file name')
	clean_name='/'.join(parts);encoding=str(file_obj.get('encoding')or'utf-8').strip().lower()
	if encoding not in{'utf-8','base64'}:raise ValueError('Board file encoding must be utf-8 or base64')
	content=str(file_obj.get('content')or'')
	try:raw_bytes=base64.b64decode(content,validate=True)if encoding=='base64'else content.encode('utf-8')
	except Exception as error:raise ValueError('Invalid board file content')from error
	digest=hashlib.sha256(raw_bytes).hexdigest();expected=str(file_obj.get('sha256')or'').strip().lower()
	if expected and expected!=digest:raise ValueError('Board file SHA-256 mismatch')
	safe_sender=encode_board_branch_dir(sender_branch or'session');target=Path(state_dir)/'board-files'/safe_sender/clean_name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(raw_bytes);return{'name':clean_name,'encoding':encoding,'content':content,'size':len(raw_bytes),'sha256':digest,'staged_path':str(target.relative_to(Path(state_dir)))}
def board_patch_apply_hint(entry,state_dir=None,repo_root=None):
	if not isinstance(entry,dict):return''
	file_obj=entry.get('file')if isinstance(entry.get('file'),dict)else None
	if file_obj is None:return''
	name=str(file_obj.get('name')or'').strip();staged_rel=str(file_obj.get('staged_path')or'').strip()
	if not(name or staged_rel).lower().endswith('.patch'):return''
	if not staged_rel:sender=str(entry.get('branch')or entry.get('session_id')or'').strip();staged_rel=f"board-files/{encode_board_branch_dir(sender)}/{name}"if sender else name
	full_path=Path(state_dir)/staged_rel if state_dir is not None else Path(staged_rel);display_path=full_path.as_posix()
	if repo_root is not None:
		try:display_path=full_path.resolve().relative_to(Path(repo_root).resolve()).as_posix()
		except ValueError:display_path=full_path.as_posix()
	return f"patch hint: git apply --3way {shlex.quote(display_path)}"
GREEK_LETTERS='alpha','beta','gamma','delta','epsilon','zeta','eta','theta','iota','kappa','lambda','mu','nu','xi','omicron','pi','rho','sigma','tau','upsilon','phi','chi','psi','omega'
WORKER_ID_RE=re.compile('^[A-Za-z0-9_-]{1,6}$')
def clean_board_repo(repo='',fallback_branch=''):
	text=str(repo or'').strip()
	if'/'in text:text=text.rsplit('/',1)[-1].strip()
	if text:return text
	tail=branch_slug_tail(fallback_branch)
	if'-'in tail:
		slug=tail.split('-',1)[1].strip()
		if slug:return slug
	return''
def clean_worker_id(raw_value,*,required=False):
	text=str(raw_value or'').strip()
	if text.lower().startswith('worker-'):text=text[7:].strip()
	if not text:
		if required:raise ValueError('Worker identifier must be 1 to 6 characters')
		return''
	if text in GREEK_LETTERS:return text
	if not WORKER_ID_RE.match(text):raise ValueError('Worker identifier must be 1 to 6 characters')
	return text
def is_legacy_hex_worker_id(branch,worker_id):
	wid=str(worker_id or'').strip().lower()
	if not wid or wid in GREEK_LETTERS:return False
	tail=branch_slug_tail(branch);head=tail.split('-')[0]if'-'in tail else tail;token=re.sub('[^A-Za-z0-9]+','',head)[:6].lower();return bool(token and wid==token)
def default_worker_id(branch,repo='',index=1):
	if 1<=index<len(GREEK_LETTERS):return GREEK_LETTERS[index]
	tail=branch_slug_tail(branch)
	if repo and tail.endswith(f"-{repo}")and len(tail)>len(repo)+1:tail=tail[:-(len(repo)+1)]
	token=re.sub('[^A-Za-z0-9]+','',tail.split('-')[0]if'-'in tail else tail)[:6];return token or'beta'
def format_board_role_id(role,repo='',worker_id='',multi_repo=False,fallback_sid=''):
	_=multi_repo;clean_repo=clean_board_repo(repo,fallback_sid)
	if role=='main':return f"{clean_repo}-alpha"if clean_repo else'alpha'
	if role=='worker':wid=clean_worker_id(worker_id)if worker_id else default_worker_id(fallback_sid,clean_repo);return f"{clean_repo}-{wid}"if clean_repo else wid
	return str(fallback_sid or'')
def resolve_board_target(target,sessions,allowed=(),main_sid=''):
	raw_token=str(target or'').strip().lstrip('@')
	if raw_token in{'*','all'}:return''
	clean=board_branch(raw_token)
	if not clean:return''
	for row in sessions or():
		row_br=row.get('branch')or''
		if row_br==clean or row.get('board_id')==clean:return row_br
		if'/'not in clean and branch_slug_tail(row_br)==clean:return row_br
	for row in sessions or():
		row_br=row.get('branch')or'';wid=row.get('worker_id')or'';row_repo=clean_board_repo(row.get('repo')or'',row_br)
		if wid and clean in{wid,f"worker-{wid}",f"{row_repo}-{wid}"if row_repo else wid,f"worker-{wid}-{row_repo}"if row_repo else f"worker-{wid}"}:return row_br
		if row.get('role')=='main'and clean in{f"{row_repo}-alpha"if row_repo else'alpha',f"main-{row_repo}"if row_repo else'main'}:return row_br
	if clean in{'alpha','main'}or clean.startswith('main-')or clean.endswith('-alpha'):
		if main_sid:return main_sid
		if allowed:return allowed[0]
	return clean
def valid_board_targets(sessions,allowed=(),main_sid=''):
	allowed_set={str(item)for item in allowed or()if item};targets=[];seen=set()
	def _add(token):
		val=str(token or'').strip()
		if val and val not in seen:seen.add(val);targets.append(val)
	if main_sid or allowed_set:_add('main')
	for row in sessions or():
		if not isinstance(row,dict):continue
		row_br=str(row.get('branch')or'')
		if not row_br or row_br not in allowed_set:continue
		if row.get('worker_id'):_add(row['worker_id'])
		if row.get('board_id')and row.get('board_id')!=row_br:_add(row['board_id'])
	for allow_br in allowed or():_add(allow_br)
	return targets
def normalize_board_mentions(text,sessions,allowed=(),main_sid='',from_sid='',to_sid=''):
	raw=str(text or'')
	if'@'not in raw:return raw
	known_sids={str(r.get('branch')or'')for r in sessions or()if isinstance(r,dict)and r.get('branch')};known_sids.update(str(s)for s in allowed or()if s)
	def _repl(match):
		prefix,token=match.group(1),match.group(2);cand=resolve_board_target(token,sessions,allowed=allowed,main_sid=main_sid)
		if cand==from_sid and to_sid and to_sid!='*'and to_sid!=from_sid and to_sid in known_sids:cand=to_sid
		if cand and cand in known_sids:return f"{prefix}@{cand}"
		return match.group(0)
	return re.sub('(^|(?<=\\s))@([A-Za-z0-9_/-]{1,64})\\b',_repl,raw)
def resolve_board_ack_sender(raw_sender,sessions,allowed=(),main_sid='',from_sid='',to_sid=''):
	clean=str(raw_sender or'').strip()
	if not clean:return None
	resolved=resolve_board_target(clean,sessions,allowed=allowed,main_sid=main_sid)
	if resolved==from_sid and to_sid and to_sid!='*'and to_sid!=from_sid:resolved=to_sid
	return resolved or clean
def apply_task_op_to_list(task_list,op):
	items=[dict(row)for row in task_list or[]if isinstance(row,dict)];op_name=str(op.get('op')or op.get('operation')or'set').strip().lower()
	if op_name=='clear':return[]
	if op_name=='replace':raw_tasks=op.get('tasks')if isinstance(op.get('tasks'),list)else[];return[dict(row)for row in raw_tasks if isinstance(row,dict)and row.get('id')]
	if op_name in{'remove','delete'}:target_id=str(op.get('task_id')or op.get('id')or'').strip();return[row for row in items if str(row.get('id')or'')!=target_id]
	if op_name=='reorder':
		order_ids=op.get('order_ids')
		if isinstance(order_ids,list)and order_ids:
			by_id={str(row.get('id')or''):row for row in items if row.get('id')};reordered=[]
			for(idx,tid)in enumerate(order_ids,start=1):
				key=str(tid or'')
				if key in by_id:entry=dict(by_id.pop(key));entry['order']=idx;reordered.append(entry)
			for entry in items:
				key=str(entry.get('id')or'')
				if key in by_id:copy=dict(by_id.pop(key));copy['order']=len(reordered)+1;reordered.append(copy)
			return reordered
		target_id=str(op.get('task_id')or op.get('id')or'').strip();target_order=int(op.get('order')or 1);chosen=next((row for row in items if str(row.get('id')or'')==target_id),None)
		if chosen is None:return items
		rest=[row for row in items if str(row.get('id')or'')!=target_id];idx=max(0,min(len(rest),target_order-1));rest.insert(idx,dict(chosen))
		for(i,row)in enumerate(rest,start=1):row['order']=i
		return rest
	target_id=str(op.get('task_id')or op.get('id')or'').strip()
	if not target_id:return items
	status='finished'if op_name=='finish'else str(op.get('status')or'').strip().lower();existing=next((row for row in items if str(row.get('id')or'')==target_id),None);details_in=op.get('details')
	if isinstance(details_in,str):details_in=[details_in]
	elif not isinstance(details_in,list):details_in=None
	if existing is None:record={'id':target_id,'title':str(op.get('title')or target_id),'status':status if status in{'upcoming','finished'}else'upcoming','type':str(op.get('task_type')or op.get('type')or'code'),'details':[str(x)for x in details_in or[]],'order':int(op.get('order')or len(items)+1),'report_id':op.get('report_id'),'updated_at':board_stamp(op.get('at'))};items.append(record)
	else:
		if op.get('title'):existing['title']=str(op.get('title'))
		if status in{'upcoming','finished'}:existing['status']=status
		if op.get('task_type')or op.get('type'):existing['type']=str(op.get('task_type')or op.get('type'))
		if details_in is not None:
			if op.get('append'):existing['details']=[*(existing.get('details')or[]),*[str(x)for x in details_in]]
			else:existing['details']=[str(x)for x in details_in]
		if op.get('order')is not None:existing['order']=int(op.get('order'))
		if'report_id'in op:existing['report_id']=op.get('report_id')
		existing['updated_at']=board_stamp(op.get('at'))
	return items
def apply_report_op_to_list(report_list,op):
	items=[dict(row)for row in report_list or[]if isinstance(row,dict)];op_name=str(op.get('op')or op.get('operation')or'answer').strip().lower();target_id=str(op.get('report_id')or op.get('id')or'').strip()
	if not target_id:return items
	op_ms=board_stamp_ms(op.get('at'))
	if op_name in{'unpublish','remove','delete'}:
		kept=[]
		for row in items:
			if str(row.get('id')or'')!=target_id:kept.append(row);continue
			row_ms=board_stamp_ms(row.get('updated_at')or row.get('published_at')or'')
			if op_ms is not None and row_ms is not None and op_ms<row_ms:kept.append(row)
		return kept
	if op_name in{'answer','submit'}:
		sub_id=str(op.get('submission_id')or op.get('note_id')or'').strip();sub_at=board_stamp(op.get('at'))
		for row in items:
			if str(row.get('id')or'')==target_id:
				row['answered']=True;row['needs_answer']=False;row['latest_answers']=dict(op.get('answers'))if isinstance(op.get('answers'),dict)else{}
				if sub_id:row['latest_answer_id']=sub_id
				row['latest_answer_at']=sub_at
	return items
BOARD_SESSION_TTL_SECONDS=180
def board_stamp_epoch(value):
	text=str(value or'').strip()
	if not text:return None
	m=re.match('^(\\d{4}-\\d{2}-\\d{2}T\\d{2}:\\d{2}:\\d{2}(?:\\.\\d+)?(?:Z|[+-]\\d{2}:\\d{2})?)',text)
	if m:text=m.group(1)
	try:
		if text.endswith('Z'):text=text[:-1]+'+00:00'
		dt=datetime.fromisoformat(text)
		if dt.tzinfo is not None:dt=dt.astimezone(UTC).replace(tzinfo=None)
		return dt.replace(tzinfo=UTC).timestamp()
	except Exception:return None
def trim_report_ops(ops):
	valid=[]
	for row in ops or():
		if not isinstance(row,dict)or not row.get('id')or not row.get('target'):continue
		copy_row=dict(row)
		if copy_row.get('at'):copy_row['at']=board_stamp(copy_row['at'])
		valid.append(copy_row)
	valid.sort(key=lambda item:(board_stamp_ms(item.get('at'))if board_stamp_ms(item.get('at'))is not None else-1,str(item.get('id')or'')));return valid
def board_survey(survey,allowed):
	result=dict(survey)
	if'answered_at'in result:result['answered_at']=board_stamp_or_none(result.get('answered_at'))
	if not allowed and isinstance(result.get('results'),list):result['results']=[{key:value for(key,value)in row.items()if key!='answer'}if isinstance(row,dict)else row for row in result['results']]
	return result
def _is_expired_unopted_disconnected_row(row,is_allowed,is_disc,now_epoch):
	if is_allowed or not is_disc:return False
	stamp_epoch=board_stamp_epoch(row.get('updated_at'))
	if stamp_epoch is None:stamps=[ts for key in('turn_ended_at','agent_call_ended_at','agent_seen_at')if(ts:=board_stamp_epoch(row.get(key)))is not None];stamp_epoch=max(stamps)if stamps else None
	return stamp_epoch is not None and now_epoch-stamp_epoch>BOARD_SESSION_TTL_SECONDS
def _is_repo_visible_on_board(row_repo,base_repo_slug,multi_repo,linked_set):
	if not base_repo_slug or not row_repo or row_repo==base_repo_slug:return True
	if linked_set:return row_repo in linked_set
	return bool(multi_repo)
def normalize_board_state(raw,repo='',branch='',polling=False,scope_repos=True,now_epoch=None):
	data=raw if isinstance(raw,dict)else{};now_sec=time.time()if now_epoch is None else float(now_epoch);sid=board_branch(branch);legacy_aliases={}
	if sid and'/'in sid:legacy_aliases[branch_slug_tail(sid)]=sid
	for s_row in data.get('sessions')if isinstance(data.get('sessions'),list)else[]:
		if isinstance(s_row,dict):
			s_br=board_branch(s_row.get('branch')or'');s_old=board_branch(s_row.get('branch')or'')
			if s_br and s_old and s_old!=s_br:legacy_aliases[s_old]=s_br
	def norm_key(val):clean=board_branch(val);return legacy_aliases.get(clean,clean)
	linked_repos=[]
	for item in data.get('linked_repos')if isinstance(data.get('linked_repos'),list)else[]:
		slug=clean_board_repo(item)
		if slug and slug not in linked_repos:linked_repos.append(slug)
	egress_version=str(data.get('egress_version')or'').strip();multi_repo=bool(data.get('multi_repo')or linked_repos);allowed=[]
	for item in data.get('allowed_sessions')if isinstance(data.get('allowed_sessions'),list)else[]:
		norm=norm_key(item)
		if norm and norm not in allowed:allowed.append(norm)
	allowed_set_init=set(allowed);removed_sessions=[]
	for item in data.get('removed_sessions')if isinstance(data.get('removed_sessions'),list)else[]:
		norm_rem=norm_key(item)
		if norm_rem and norm_rem!=sid and norm_rem not in allowed_set_init and norm_rem not in removed_sessions:removed_sessions.append(norm_rem)
	removed_set=set(removed_sessions);raw_worker_ids=data.get('worker_ids')if isinstance(data.get('worker_ids'),dict)else{};worker_ids={}
	for(k,v)in raw_worker_ids.items():
		norm_k=norm_key(k)
		try:clean_v=clean_worker_id(v)
		except ValueError:clean_v=''
		if norm_k and norm_k not in removed_set and clean_v and not is_legacy_hex_worker_id(norm_k,clean_v):worker_ids[norm_k]=clean_v
	raw_worker_tasks=data.get('worker_tasks')if isinstance(data.get('worker_tasks'),dict)else{};worker_tasks={}
	for(k,v)in raw_worker_tasks.items():
		norm_k=norm_key(k)
		if norm_k and norm_k not in removed_set and isinstance(v,list):worker_tasks[norm_k]=[dict(row)for row in v if isinstance(row,dict)and row.get('id')]
	raw_task_ops=data.get('task_ops')if isinstance(data.get('task_ops'),list)else[];task_ops=[dict(row)for row in raw_task_ops if isinstance(row,dict)and row.get('id')and row.get('target')];raw_worker_reports=data.get('worker_reports')if isinstance(data.get('worker_reports'),dict)else{};worker_reports={}
	for(k,v)in raw_worker_reports.items():
		norm_k=norm_key(k)
		if norm_k and norm_k not in removed_set and isinstance(v,list):worker_reports[norm_k]=[dict(row)for row in v if isinstance(row,dict)and row.get('id')]
	report_ops=trim_report_ops(data.get('report_ops'));raw_sessions_list=data.get('sessions')if isinstance(data.get('sessions'),list)else[]
	for row in raw_sessions_list:
		if not isinstance(row,dict):continue
		row_sid=norm_key(row.get('branch')or row.get('branch')or'')
		if not row_sid or row_sid in removed_set or sid and row_sid==sid:continue
		if branch_slug_tail(row_sid).startswith('probe-')and row_sid not in allowed:
			removed_set.add(row_sid)
			if row_sid not in removed_sessions:removed_sessions.append(row_sid)
			allowed=[item for item in allowed if item!=row_sid];worker_ids.pop(row_sid,None);worker_tasks.pop(row_sid,None);worker_reports.pop(row_sid,None)
	main_sid=norm_key(data.get('main_session_id')or'')
	if main_sid and(main_sid not in allowed or main_sid in removed_set):main_sid=''
	if main_sid in worker_ids:worker_ids.pop(main_sid,None)
	wakes={norm_key(k):v for(k,v)in data.get('wakes',{}).items()if norm_key(k)and norm_key(k)not in removed_set and isinstance(v,(int,float,str))}if isinstance(data.get('wakes'),dict)else{};sessions=[];seen_sids=set()
	for row in raw_sessions_list:
		if not isinstance(row,dict):continue
		row_sid=norm_key(row.get('branch')or row.get('branch')or'')
		if not row_sid or row_sid in seen_sids or row_sid in removed_set or branch_slug_tail(row_sid).startswith('probe-')and row_sid not in allowed:continue
		is_self=bool(sid and row_sid==sid);wake_pending=row_sid in allowed and bool(wakes.get(row_sid));row_disc=False if is_self else bool(row.get('disconnected')or row.get('status')=='disconnected')
		if not is_self and _is_expired_unopted_disconnected_row(row,row_sid in allowed,row_disc,now_sec):continue
		seen_sids.add(row_sid)
		if row.get('worker_id')and row_sid!=main_sid and row.get('role')!='main'and row_sid not in worker_ids:
			try:
				delegated=clean_worker_id(row.get('worker_id'))
				if delegated and not is_legacy_hex_worker_id(row_sid,delegated):worker_ids[row_sid]=delegated
			except ValueError:pass
		row_polling=(bool(polling)if is_self else bool(row.get('polling')or row.get('status')=='polling'))and not wake_pending and not row_disc;row_repo=str(row.get('repo')or(repo if is_self else''));row_role='main'if main_sid==row_sid or not main_sid and row_sid in allowed else'worker'if row_sid in allowed else'blocked';row_wid=worker_ids.get(row_sid)or default_worker_id(row_sid,row_repo)if row_role=='worker'else'';s_entry={'branch':row_sid,'board_id':format_board_role_id(row_role,repo=row_repo,worker_id=row_wid,multi_repo=multi_repo,fallback_sid=row_sid),'worker_id':row_wid,'repo':row_repo,'status':'disconnected'if row_disc else'polling'if row_polling else'active','polling':row_polling,'disconnected':row_disc,'updated_at':board_stamp(row.get('updated_at'))if isinstance(row.get('updated_at'),str)and re.match('^\\d{4}-\\d{2}-\\d{2}T',row['updated_at'])else board_stamp(),'allowed':row_sid in allowed,'role':row_role}
		if isinstance(row.get('survey'),dict):s_entry['survey']=board_survey(row['survey'],row_sid in allowed)
		for status_key in('polling_since','waiting_for_gates','checking_pr','calls_since_message','gate_threshold','agent_seen_at','turn_ended_at','agent_call_ended_at'):
			if status_key in row and row[status_key]is not None:
				if status_key in{'polling_since','agent_seen_at','turn_ended_at','agent_call_ended_at'}:
					norm_stamp=board_stamp_or_none(row[status_key])
					if norm_stamp is not None:s_entry[status_key]=norm_stamp
				else:s_entry[status_key]=row[status_key]
		for sha_key in('remote_head','origin_main_head'):
			raw_sha=row.get(sha_key)
			if isinstance(raw_sha,str):
				clean_sha=raw_sha.strip().lower()
				if GIT_SHA_RE.fullmatch(clean_sha):s_entry[sha_key]=clean_sha
		sessions.append(s_entry)
	if sid and sid not in seen_sids:seen_sids.add(sid);self_polling=bool(polling)and not(sid in allowed and bool(wakes.get(sid)));self_repo=str(repo or'');self_role='main'if main_sid==sid or not main_sid and sid in allowed else'worker'if sid in allowed else'blocked';self_wid=worker_ids.get(sid)or default_worker_id(sid,self_repo)if self_role=='worker'else'';sessions.insert(0,{'branch':sid,'board_id':format_board_role_id(self_role,repo=self_repo,worker_id=self_wid,multi_repo=multi_repo,fallback_sid=sid),'worker_id':self_wid,'repo':self_repo,'status':'polling'if self_polling else'active','polling':self_polling,'updated_at':board_stamp(),'allowed':sid in allowed,'role':self_role})
	for allowed_sid in allowed:
		if allowed_sid not in seen_sids:seen_sids.add(allowed_sid);allowed_repo=clean_board_repo('',allowed_sid)or str(repo or'');allowed_role='main'if main_sid==allowed_sid or not main_sid else'worker';allowed_wid=worker_ids.get(allowed_sid,'')if allowed_role=='worker'else'';sessions.append({'branch':allowed_sid,'board_id':'','worker_id':allowed_wid,'repo':allowed_repo,'status':'active','polling':False,'updated_at':board_stamp(),'allowed':True,'role':allowed_role})
	repo_has_main={}
	for row in sessions:
		row_repo=clean_board_repo(row.get('repo')or'',row['branch'])or str(repo or'');row['repo']=row_repo
		if not row.get('allowed'):row['role']='blocked';continue
		if main_sid and row['branch']==main_sid:row['role']='main';repo_has_main[row_repo]=row['branch']
	for row in sessions:
		if not row.get('allowed'):continue
		row_repo=row['repo']
		if row_repo not in repo_has_main:row['role']='main';repo_has_main[row_repo]=row['branch']
		elif repo_has_main[row_repo]!=row['branch']:row['role']='worker'
	used_by_repo={}
	for row in sessions:
		row_repo=row['repo'];used=used_by_repo.setdefault(row_repo,{'alpha'})
		if row['role']=='worker':
			explicit_wid=worker_ids.get(row['branch'])
			if explicit_wid:used.add(explicit_wid)
	for row in sessions:
		row_repo=row['repo']
		if row['role']=='main':row['worker_id']='';row['board_id']=format_board_role_id('main',repo=row_repo,multi_repo=multi_repo,fallback_sid=row['branch'])
		elif row['role']=='worker':
			wid=worker_ids.get(row['branch'])or''
			if not wid:
				used=used_by_repo.setdefault(row_repo,{'alpha'})
				for letter in GREEK_LETTERS[1:]:
					if letter not in used:wid=letter;break
				if not wid:wid=default_worker_id(row['branch'],row_repo)
				used.add(wid)
			row['worker_id']=wid;row['board_id']=format_board_role_id('worker',repo=row_repo,worker_id=wid,multi_repo=multi_repo,fallback_sid=row['branch'])
		else:row['worker_id']='';row['board_id']=row['branch']
	session_by_id={row['branch']:row for row in sessions};entries=[]
	for item in data.get('entries')if isinstance(data.get('entries'),list)else[]:
		if not isinstance(item,dict):continue
		from_sid=resolve_board_target(item.get('branch')or item.get('session_id')or'',sessions,allowed=allowed,main_sid=main_sid)
		if not from_sid:continue
		raw_to=str(item.get('to')or'*');resolved_to='*'if raw_to=='*'else resolve_board_target(raw_to,sessions,allowed=allowed,main_sid=main_sid);item_payload=dict(item.get('payload'))if isinstance(item.get('payload'),dict)else{}
		for k in('tag','text'):
			if item.get(k)and k not in item_payload:item_payload[k]=str(item.get(k))
		entry_text=str(item.get('text')or item_payload.get('text')or item_payload.get('summary')or'');mention_target='';raw_mention=str(item.get('mention_target')or'').strip()
		if raw_mention:mention_target=resolve_board_target(raw_mention,sessions,allowed=allowed,main_sid=main_sid)
		if not mention_target and entry_text:
			for token in re.findall('(?:^|(?<=\\s))@([A-Za-z0-9_/-]{1,64})\\b',entry_text):
				cand=resolve_board_target(token,sessions,allowed=allowed,main_sid=main_sid)
				if cand and cand in allowed and cand!=from_sid:mention_target=cand;break
		if mention_target and resolved_to=='*':resolved_to=mention_target
		entry_text=normalize_board_mentions(entry_text,sessions,allowed=allowed,main_sid=main_sid,from_sid=from_sid,to_sid=resolved_to)
		if'text'in item_payload:item_payload['text']=entry_text
		sender_row=session_by_id.get(from_sid)or{};raw_replies=item.get('replies');replies=[]
		if isinstance(raw_replies,list):
			for r in raw_replies:
				if not isinstance(r,dict):continue
				r_copy=dict(r)
				if r_copy.get('text'):r_copy['text']=normalize_board_mentions(r_copy['text'],sessions,allowed=allowed,main_sid=main_sid,from_sid=resolved_to if resolved_to!='*'else'',to_sid=from_sid)
				if r_copy.get('sender'):r_copy['sender']=resolve_board_ack_sender(r_copy['sender'],sessions,allowed=allowed,main_sid=main_sid,from_sid=from_sid,to_sid=resolved_to)
				if r_copy.get('at'):r_copy['at']=board_stamp(r_copy['at'])
				replies.append(r_copy)
		raw_ack_text=item.get('ack_text');norm_ack_text=normalize_board_mentions(str(raw_ack_text or''),sessions,allowed=allowed,main_sid=main_sid,from_sid=resolved_to if resolved_to!='*'else'',to_sid=from_sid)if raw_ack_text is not None else None;norm_ack_sender=resolve_board_ack_sender(item.get('ack_sender'),sessions,allowed=allowed,main_sid=main_sid,from_sid=from_sid,to_sid=resolved_to);entries.append({'id':str(item.get('id')or f"p{uuid.uuid4().hex[:5]}"),'branch':from_sid,'board_id':str(sender_row.get('board_id')or item.get('board_id')or from_sid),'from_owner':bool(item.get('from_owner')),'note_id':str(item.get('note_id')or''),'to':resolved_to or'*','mention_target':mention_target,'repo':str(item.get('repo')or sender_row.get('repo')or''),'at':board_stamp(item.get('at')),'tag':str(item.get('tag')or item_payload.get('tag')or''),'text':entry_text,'seen_at':board_stamp_or_none(item.get('seen_at')),'acknowledged_at':board_stamp_or_none(item.get('acknowledged_at')),'ack_type':str(item.get('ack_type')or'reply')if item.get('acknowledged_at')else None,'ack_text':norm_ack_text,'ack_sender':norm_ack_sender,'ack_edited_at':board_stamp_or_none(item.get('ack_edited_at')),'ack_edited_seen_count':int(item.get('ack_edited_seen_count')or 0),'task_id':str(item.get('task_id')or'')if item.get('task_id')else None,'replies':replies,'payload':item_payload,'file':item.get('file')if isinstance(item.get('file'),dict)else None})
	self_row=next((r for r in sessions if r['branch']==sid),None)if sid else None;is_worker=bool(self_row and self_row.get('allowed')and self_row.get('role')=='worker');is_main=not is_worker;self_role='worker'if is_worker else'main';self_wid=(self_row['worker_id']if self_row and self_row.get('worker_id')else worker_ids.get(sid)or default_worker_id(sid,str(repo or'')))if self_role=='worker'and sid else'';self_board_id=self_row['board_id']if self_row and self_row.get('board_id')and self_row.get('allowed')else format_board_role_id(self_role,repo=str(repo or''),worker_id=self_wid,multi_repo=multi_repo,fallback_sid=sid)if sid else'';known_repos=[];base_repo_slug=clean_board_repo(repo,sid)
	if base_repo_slug:known_repos.append(base_repo_slug)
	if isinstance(data.get('known_repos'),list):
		for item in data['known_repos']:
			r_slug=clean_board_repo(item,'')
			if r_slug and r_slug not in known_repos:known_repos.append(r_slug)
	for row in sessions:
		r_slug=clean_board_repo(row.get('repo')or'',row.get('branch')or'')
		if r_slug and(row.get('allowed')or r_slug==base_repo_slug)and r_slug not in known_repos:known_repos.append(r_slug)
	for r_slug in linked_repos:
		if r_slug not in known_repos:known_repos.append(r_slug)
	if scope_repos and base_repo_slug:linked_set=set(linked_repos);sessions=[row for row in sessions if row['branch']==sid or _is_repo_visible_on_board(clean_board_repo(row.get('repo')or'',row['branch']),base_repo_slug,multi_repo,linked_set)]
	effective_main_sid=repo_has_main.get(base_repo_slug,'')if base_repo_slug else main_sid
	if sid and sid not in allowed and not effective_main_sid:effective_main_sid=''
	return{'version':1,'egress_version':egress_version,'branch':sid,'board_id':self_board_id,'worker_id':self_wid,'multi_repo':multi_repo,'linked_repos':linked_repos,'known_repos':known_repos,'worker_ids':worker_ids,'worker_tasks':worker_tasks,'task_ops':task_ops,'worker_reports':worker_reports,'report_ops':report_ops,'repo':str(repo or''),'removed_sessions':removed_sessions,'allowed_sessions':allowed,'main_session_id':effective_main_sid,'board_role':self_role,'can_push_or_pr':bool(is_main),'sessions':sessions,'entries':entries,'wakes':wakes}
def board_main_session_warning(state_dir,command):
	if not isinstance(command,str)or not command.strip():return None
	if not re.search('(?:^|[;&|()\\s])(?:git\\s+(?:-\\S+\\s+)*push\\b|gh\\s+pr\\s+(?:create|edit|merge|close|ready|reopen)\\b)',command):return None
	if not state_file(state_dir).is_file():return None
	try:board=Store(state_dir,create=False).board_state()
	except Exception:return None
	main_sid=board.get('main_session_id')or''
	if main_sid and board.get('board_role')=='worker':return f"MAIN SESSION GATE: session {board.get('branch')!r} is a worker session while {main_sid!r} is the Main Session. Only the Main Session may run git push or mutate pull requests. Send files or patches to the Main Session via the multi-session board."
	return None
_PREVIEW_BOARD_COLLECTION_KEYS=frozenset({'sessions','entries','task_ops','report_ops','worker_tasks','worker_reports','wakes'})
def _preview_canonical_json(value):return json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':'))
def _preview_state_record_group(record):
	if not isinstance(record,dict):return'notes'
	if'markdown'in record:return'reports'
	if'title'in record:return'tasks'
	if'report_id'in record:return'answers'
	return'notes'
def _preview_state_record_key(record):
	if not isinstance(record,dict):return hashlib.sha256(_preview_canonical_json(record).encode('utf-8')).hexdigest()[:16]
	rec_id=str(record.get('id')or'').strip()
	if rec_id:return rec_id
	if record.get('seq')is not None:return f"seq:{record['seq']}"
	return hashlib.sha256(_preview_canonical_json(record).encode('utf-8')).hexdigest()[:16]
def compute_preview_state_diff(prev_core,next_core,base_rev,next_rev):
	prev_map={}
	for row in prev_core or[]:
		if isinstance(row,dict)and row.get('type')!='userscript-autosave':pair=_preview_state_record_group(row),_preview_state_record_key(row);prev_map[pair]=row,_preview_canonical_json(row)
	next_map={};upserts=[]
	for row in next_core or[]:
		if not isinstance(row,dict)or row.get('type')=='userscript-autosave':continue
		pair=_preview_state_record_group(row),_preview_state_record_key(row);encoded=_preview_canonical_json(row);next_map[pair]=row,encoded;prev_item=prev_map.get(pair)
		if prev_item is None or prev_item[1]!=encoded:upserts.append(row)
	tombstones=[{'group':grp,'key':rec_key}for(grp,rec_key)in sorted(prev_map)if(grp,rec_key)not in next_map];return{'base_rev':int(base_rev),'rev':int(next_rev),'upserts':upserts,'tombstones':tombstones}
def _decompose_preview_board(board):
	data=board if isinstance(board,dict)else{};records={('meta','scalars'):{key:val for(key,val)in data.items()if key not in _PREVIEW_BOARD_COLLECTION_KEYS}}
	for(idx,row)in enumerate(data.get('sessions')or[]):
		if isinstance(row,dict):
			sid=str(row.get('branch')or row.get('session_id')or f"idx:{idx}").strip()
			if sid:records['session',sid]=row
	for(idx,row)in enumerate(data.get('entries')or[]):
		if isinstance(row,dict):
			eid=str(row.get('id')or f"idx:{idx}").strip()
			if eid:records['entry',eid]=row
	for(idx,row)in enumerate(data.get('task_ops')or[]):
		if isinstance(row,dict):
			oid=str(row.get('id')or f"idx:{idx}").strip()
			if oid:records['task_op',oid]=row
	for(idx,row)in enumerate(data.get('report_ops')or[]):
		if isinstance(row,dict):
			oid=str(row.get('id')or f"idx:{idx}").strip()
			if oid:records['report_op',oid]=row
	wt=data.get('worker_tasks')
	if isinstance(wt,dict):
		for(sid,items)in wt.items():
			clean_sid=str(sid or'').strip()
			if clean_sid and isinstance(items,list):records['worker_tasks',clean_sid]=items
	wr=data.get('worker_reports')
	if isinstance(wr,dict):
		for(sid,items)in wr.items():
			clean_sid=str(sid or'').strip()
			if clean_sid and isinstance(items,list):records['worker_reports',clean_sid]=items
	wakes=data.get('wakes')
	if isinstance(wakes,dict):
		for(sid,stamp)in wakes.items():
			clean_sid=str(sid or'').strip()
			if clean_sid and stamp:records['wake',clean_sid]=stamp
	return records
def _recompose_preview_board(records):
	out={'multi_repo':False,'linked_repos':[],'removed_sessions':[],'worker_ids':{},'worker_tasks':{},'task_ops':[],'worker_reports':{},'report_ops':[],'allowed_sessions':[],'main_session_id':'','sessions':[],'entries':[],'wakes':{}};meta=records.get(('meta','scalars'))
	if isinstance(meta,dict):
		for(key,val)in meta.items():out[key]=val
	sessions=[];entries=[];task_ops=[];report_ops=[]
	for((rec_type,rec_key),val)in records.items():
		if rec_type=='meta':continue
		if rec_type=='session'and isinstance(val,dict):sessions.append(dict(val))
		elif rec_type=='entry'and isinstance(val,dict):entries.append(dict(val))
		elif rec_type=='task_op'and isinstance(val,dict):task_ops.append(dict(val))
		elif rec_type=='report_op'and isinstance(val,dict):report_ops.append(dict(val))
		elif rec_type=='worker_tasks'and isinstance(val,list):out['worker_tasks'][rec_key]=list(val)
		elif rec_type=='worker_reports'and isinstance(val,list):out['worker_reports'][rec_key]=list(val)
		elif rec_type=='wake'and val:out['wakes'][rec_key]=val
	entries.sort(key=lambda r:(str(r.get('created_at')or r.get('at')or''),str(r.get('id')or'')));task_ops.sort(key=lambda r:(str(r.get('at')or''),str(r.get('id')or'')));report_ops.sort(key=lambda r:(str(r.get('at')or''),str(r.get('id')or'')));out['sessions']=sessions;out['entries']=entries;out['task_ops']=task_ops;out['report_ops']=report_ops;return out
def compute_preview_board_diff(prev_board,next_board,base_rev,next_rev):
	prev_records=_decompose_preview_board(prev_board);next_records=_decompose_preview_board(next_board);upserts=[]
	for(pair,val)in next_records.items():
		encoded=_preview_canonical_json(val)
		if pair not in prev_records or _preview_canonical_json(prev_records[pair])!=encoded:upserts.append({'type':pair[0],'key':pair[1],'value':val})
	tombstones=[{'type':pair[0],'key':pair[1]}for pair in sorted(prev_records)if pair not in next_records];return{'base_rev':int(base_rev),'rev':int(next_rev),'upserts':upserts,'tombstones':tombstones}
def apply_preview_board_diff(base_board,diff_obj):
	if not isinstance(diff_obj,dict):raise TypeError('Board diff must be an object')
	upserts=diff_obj.get('upserts');tombstones=diff_obj.get('tombstones')
	if not isinstance(upserts,list)or not isinstance(tombstones,list):raise TypeError('Board diff requires upserts and tombstones arrays')
	records=_decompose_preview_board(base_board)
	for tomb in tombstones:
		if isinstance(tomb,dict):
			rec_type=str(tomb.get('type')or'').strip();rec_key=str(tomb.get('key')or'').strip()
			if rec_type and rec_key:records.pop((rec_type,rec_key),None)
	for item in upserts:
		if isinstance(item,dict):
			rec_type=str(item.get('type')or'').strip();rec_key=str(item.get('key')or'').strip()
			if rec_type and rec_key and'value'in item:records[rec_type,rec_key]=item['value']
	return _recompose_preview_board(records)
def autosave_core_and_stamp(records):
	if not isinstance(records,list)or not all(isinstance(row,dict)for row in records):raise TypeError('Autosave core must be an array of records')
	if any(row.get('type')=='userscript-autosave'for row in records):raise ValueError('Autosave core cannot contain history')
	sources={row.get('id'):row.get('source')for row in records if'markdown'in row and isinstance(row.get('id'),str)}
	with tempfile.TemporaryDirectory()as directory:
		checked=Store(directory,create=True)
		if records:checked.import_state(json.dumps(records))
		core,_=checked.state_lines(checked.state()['notes'],checked.tasks()or{});stamp=checked.newest_stamp()
	order=dict.fromkeys(TASK_STATUSES,0)
	for row in core:
		if'markdown'in row and row.get('id')in sources:row['source']=sources[row['id']]
		status=row.get('status')
		if status in order and'title'in row and'markdown'not in row:order[status]+=1;row['order']=order[status]
	return core,stamp
def autosave_core(records):return autosave_core_and_stamp(records)[0]
def autosave_digest(core):encoded=json.dumps(core,sort_keys=True,ensure_ascii=False,separators=(',',':'));return hashlib.sha256(encoded.encode('utf-8')).hexdigest()
def autosave_scope_key(scope):
	if not isinstance(scope,dict)or set(scope)!={'repo','branch'}:raise TypeError('Autosave scope needs repository and branch')
	if not all(isinstance(value,str)for value in scope.values())or not scope['repo']:raise ValueError('Invalid autosave scope')
	return json.dumps(scope,sort_keys=True)
def autosave_record(record):
	if record.get('type')!='userscript-autosave'or type(record.get('version'))is not int or record['version']!=1:raise ValueError('Unsupported autosave history version')
	if not isinstance(record.get('save_id'),str):raise TypeError('Autosave save ID must be a UUID string')
	if set(record)-{'type','version','save_id','scope','digest','core','stamp','confirmed_at','receipts'}:raise ValueError('Unsupported autosave history fields')
	uuid.UUID(record['save_id']);autosave_scope_key(record['scope']);core=autosave_core(record['core']);digest=autosave_digest(core);raw_digest=record.get('digest')
	if raw_digest not in(digest,autosave_digest(record['core'])):raise ValueError('Autosave history digest does not match')
	if record.get('stamp')is not None and import_stamp(record['stamp'])is None:raise ValueError('Autosave history stamp is invalid')
	if import_stamp(record.get('confirmed_at'))is None:raise ValueError('Autosave history needs a confirmation time')
	receipts=record.get('receipts',{})
	if not isinstance(receipts,dict):raise TypeError('Autosave receipts must be an object')
	for(save_id,item_digest)in receipts.items():
		uuid.UUID(save_id)
		if not isinstance(item_digest,str)or not re.fullmatch('[0-9a-f]{64}',item_digest):raise ValueError('Invalid autosave receipt digest')
	receipts=dict(receipts)
	if record['save_id']in receipts:
		if receipts[record['save_id']]not in(raw_digest,digest):raise ValueError('Autosave receipt conflicts with its saved state')
		receipts[record['save_id']]=digest
	out={**record,'core':core,'digest':digest}
	if'receipts'in record:out['receipts']=receipts
	return out
def tool_probe_command(state_dir,name):
	if not(Path(state_dir).resolve()/'state.sqlite3').is_file():return 0
	try:Store(state_dir).record_tool(name)
	except Exception:return 0
	return 0
def survey_command(state_dir,answers,smallest_image=False,root=None):
	import survey
	if smallest_image:
		if answers is not None:raise ValueError('Pass either --smallest-image or survey answer JSON, not both')
		if root is None:
			cwd=Path.cwd().resolve()
			for candidate in(cwd,*cwd.parents):
				if(candidate/'.git').exists():root=candidate;break
			else:root=cwd
		found=survey.smallest_image(root)
		if not found:return 1
		print(found);return 0
	if answers is None:print(survey.INSTRUCTIONS);print(json.dumps(survey.SPEC,indent=2,ensure_ascii=False));print(json.dumps(survey.template(),indent=2));return 0
	from http.client import HTTPConnection,HTTPException;store=Store(state_dir);require_server(store)
	try:
		with closing(HTTPConnection('127.0.0.1',int(store.meta_value('port')),timeout=15))as client:
			client.request('POST','/api/survey',answers.encode('utf-8'),{'Content-Type':'application/json'});response=client.getresponse();body=response.read()
			if response.status!=200:error=json.loads(body).get('error','Request rejected');raise ValueError(f"Survey submission: HTTP {response.status}: {error}")
	except HTTPException as error:raise ValueError(f"Survey submission failed: {error}")from None
	print('Survey received.');return 0
def mermaid_report_warnings(source_label,markdown):
	import importlib.util;checker=Path(__file__).resolve().with_name('mermaid_check.py')
	if not checker.is_file():return[]
	spec=importlib.util.spec_from_file_location('mermaid_check',checker)
	if spec is None or spec.loader is None:return[]
	module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module.format_mermaid_warnings(source_label,module.check_mermaid_markdown(markdown))
def verify_publication(store,report_id,revision,count):
	from http.client import HTTPConnection,HTTPException;failure='Publication verification failed. Check the preview before retrying.'
	try:
		with closing(HTTPConnection('127.0.0.1',int(store.meta_value('port')),timeout=15))as client:
			documents=[]
			for path in('/api/state',f"/api/reports/{report_id}/html"):
				client.request('GET',path);response=client.getresponse()
				if response.status!=200:raise ValueError(f"Publication verification failed: HTTP {response.status}.")
				documents.append(json.loads(response.read()))
	except(OSError,HTTPException,UnicodeError,json.JSONDecodeError):raise ValueError(failure)from None
	state,rendered=documents
	if not isinstance(state,dict)or not isinstance(state.get('reports'),list):raise TypeError(failure)
	matches=[row for row in state['reports']if isinstance(row,dict)and row.get('id')==report_id]
	if len(matches)!=1 or matches[0].get('updated_at')!=clip_stamp(revision)or not isinstance(rendered,dict)or rendered.get('revision')!=revision or type(rendered.get('fields'))is not int or rendered['fields']!=count or not isinstance(rendered.get('html'),str):raise ValueError(failure)
	warnings=rendered.get('warnings',[])
	if not isinstance(warnings,list):return[]
	return[warning for warning in warnings if isinstance(warning,dict)and type(warning.get('line'))is int and warning['line']>0 and warning.get('marker')in('~~','==')]
def task_steps(details):
	steps=[]
	for item in details or():steps.extend(part.strip()for part in str(item).splitlines())
	return[step for step in steps if step]
def check_task_steps(details):
	for step in details or():
		if len(step)>TASK_STEP_MAX:raise ValueError(f"A task detail is one step per line, {TASK_STEP_MAX} characters or fewer; this line runs {len(step)} ({step!r}). Split it and repeat --details")
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
def render_report_block(markdown):markdown=strip_fenced_md_comments(markdown);tagged,held=hold_out(markdown,'(?:\\s*(?:</?(?:ul|ol|li)>)\\s*){2,}',lambda match:match.group(0).strip().lower(),'listtag');out=put_back(render(tagged),held,'listtag');out=re.sub('<p>(<(?:ul|ol)>)','\\1',out);out=re.sub('(</(?:ul|ol)>)</p>','\\1',out);return out
def render_report(markdown,title=None):
	blocks,questions=parse_fields(markdown);parts=[]
	for(type,item)in blocks:
		if type=='markdown':
			if item.strip():parts.append(render_report_block(item))
		else:parts.append(field_html(item))
	rendered=''.join(parts)
	if isinstance(title,str)and title.strip()and not re.match('^\\s*<h1\\b',rendered,re.IGNORECASE):rendered=f"<h1>{html.escape(title.strip())}</h1>\n{rendered}"
	return rendered,questions
SLASH_COMMAND_RE=re.compile('^/(nuu|nemoizer)(?:\\s+([\\s\\S]*))?$',re.IGNORECASE)
NEMOIZER_COLLECT_IGNORES='arena-state','.agents','.git','node_modules','LICENSE.md','skills/arena-skill','skills/refs/arena-skill/assets/markdown-it.min.js','skills/refs/arena-skill/assets/mermaid.min.js','skills/refs/arena-skill/assets/mermaid.LICENSE','skills/refs/arena-skill/assets/gsap.LICENSE','skills/squash/LICENSE.txt','skills/refs/squash/LICENSE.txt','skills/refs/arena-skill/assets/lucide.LICENSE','skills/refs/arena-skill/assets/markdown-it.LICENSE','skills/refs/arena-skill/assets/sortable.LICENSE','skills/nuu/data/vendor','skills/refs/nuu/data/vendor'
def _find_skill_dir(root,name):
	roots=[Path(root)];here=Path(__file__).resolve();roots.extend(here.parents);cwd=Path.cwd().resolve();roots.append(cwd);roots.extend(cwd.parents)
	for base in roots:
		for candidate in(base/'.agents'/'skills'/name,base/'skills'/name):
			if candidate.is_dir():return candidate
	return Path(root)/'.agents'/'skills'/name
def _skill_rel_path(skill_dir,repo_root,name):
	try:return skill_dir.resolve().relative_to(Path(repo_root).resolve()).as_posix()
	except ValueError:return f".agents/skills/{name}"
def _extract_clauses(skill_md):
	if not skill_md.is_file():return''
	text=skill_md.read_text(encoding='utf-8')
	if'## Clauses'not in text:return''
	after=text.split('## Clauses',1)[1];section=after.split('\n## ',1)[0].strip();lines=[line.strip()for line in section.splitlines()if re.match('^\\d+\\.',line.strip())];return'\n'.join(lines)
def _extract_nuu_breakpoints(nuu_dir):
	for toml_candidate in(nuu_dir/'nuu.toml',nuu_dir/'references'/'nuu.toml'):
		if toml_candidate.is_file():
			match=re.search('named_breakpoints\\s*=\\s*\\[([^\\]]+)\\]',toml_candidate.read_text(encoding='utf-8'))
			if match:
				items=re.findall('["\\\']([^"\\\']+)["\\\']',match.group(1))
				if items:return tuple(items)
	for script_name in('nuu.py','uiux.py'):
		script_py=nuu_dir/'scripts'/script_name
		if script_py.is_file():
			match=re.search('NAMED_BREAKPOINTS\\s*=\\s*\\(([^)]+)\\)',script_py.read_text(encoding='utf-8'))
			if match:
				items=re.findall('["\\\']([^"\\\']+)["\\\']',match.group(1))
				if items:return tuple(items)
	clauses=_extract_clauses(nuu_dir/'SKILL.md')
	for line in clauses.splitlines():
		if line.startswith('7.'):
			items=re.findall('\\b[0-9.]+(?:em|rem|px)\\b',line)
			if items:return tuple(items)
	return'68em','60em','30em','24em'
def _recipe_summary_line(content):
	for raw in content.splitlines():
		line=raw.strip()
		if not line or line.startswith(('#','```')):continue
		if line.startswith('- '):line=line[2:].strip()
		return line.split('. ',1)[0].rstrip('.')+'.'
	return'Reusable UI/UX implementation pattern.'
def _collect_nuu_recipes(nuu_dir):
	recipes={};recipes_dir=nuu_dir/'references'/'recipes'
	if recipes_dir.is_dir():
		for path in sorted(recipes_dir.glob('*.md')):recipes[path.stem]=path.read_text(encoding='utf-8').strip()
	legacy_fonts=nuu_dir/'references'/'offline-fonts.md'
	if'offline-fonts'not in recipes and legacy_fonts.is_file():recipes['offline-fonts']=legacy_fonts.read_text(encoding='utf-8').strip()
	nuu_ref=nuu_dir/'references'/'nuu.md'
	if'scroll-edge-fade'not in recipes and nuu_ref.is_file():
		text=nuu_ref.read_text(encoding='utf-8')
		if'## Optional Recipes'in text:recipes['scroll-edge-fade']='# Recipe: Scroll-edge fade\n\n'+text.split('## Optional Recipes',1)[1].strip()
	return recipes
def _concise_rule_doc(markdown):
	lines=[];in_fence=False
	for raw in markdown.splitlines():
		stripped=raw.strip()
		if stripped.startswith('```'):in_fence=not in_fence;continue
		if in_fence or stripped.startswith(('Rationale:','Note:')):continue
		if stripped:lines.append(stripped)
	return'\n\n'.join(lines[:5])
def _skill_exists(root,name):
	roots=[Path(root or resolve_repo_root()).resolve()];here=Path(__file__).resolve();roots.extend(here.parents);cwd=Path.cwd().resolve();roots.append(cwd);roots.extend(cwd.parents)
	for base in roots:
		for candidate in(base/'.agents'/'skills'/name,base/'skills'/name):
			if(candidate/'SKILL.md').is_file():return candidate
	return None
def discover_slash_commands(root=None):
	commands=[];nuu_dir=_skill_exists(root,'nuu')
	if nuu_dir is not None:
		commands.extend([{'cmd':'/nuu audit','desc':'Send agent note: run a full nuu audit'},{'cmd':'/nuu help','desc':'Show nuu commands and clauses (modal)'},{'cmd':'/nuu learn','desc':'Send agent note: survey repo UI/UX for nuu'},{'cmd':'/nuu recipes','desc':'List nuu UI recipes (modal)'},{'cmd':'/nuu search ','desc':'Search the nuu UI/UX catalog (modal)'}])
		for name in sorted(_collect_nuu_recipes(nuu_dir)):commands.append({'cmd':f"/nuu recipe {name}",'desc':f"Send agent note: apply {name} recipe"})
	nemo_dir=_skill_exists(root,'nemoizer')
	if nemo_dir is not None:commands.extend([{'cmd':'/nemoizer audit','desc':'Send agent note: run a nemoizer audit and report'},{'cmd':'/nemoizer collect','desc':'Scan prose and code for nemoizer findings (modal)'},{'cmd':'/nemoizer help','desc':'Show nemoizer commands and clauses (modal)'},{'cmd':'/nemoizer learn','desc':'Send agent note: survey repo prose for nemoizer'},{'cmd':'/nemoizer recipes','desc':'Browse nemoizer templates and references (modal)'},{'cmd':'/nemoizer rules','desc':'Browse nemoizer rules or /nemoizer rules <id> (modal)'}])
	commands.sort(key=lambda item:item['cmd'].strip());return commands
def resolve_slash_command(text,root=None):
	if not isinstance(text,str):return None
	stripped=text.strip();match=SLASH_COMMAND_RE.match(stripped)
	if not match:return None
	skill=match.group(1).lower();rest=(match.group(2)or'').strip();lines=rest.splitlines()if rest else[];first_line=lines[0].strip()if lines else'';extra_lines='\n'.join(lines[1:]).strip()if len(lines)>1 else'';parts=first_line.split(None,1);sub=parts[0].lower()if parts else'';arg=parts[1].strip()if len(parts)>1 else'';trailing='\n\n'.join(part for part in(arg,extra_lines)if part).strip();repo_root=Path(root or resolve_repo_root()).resolve()
	if skill=='nuu':
		nuu_dir=_find_skill_dir(repo_root,'nuu')
		if sub in{'','help'}:
			clauses=_extract_clauses(nuu_dir/'SKILL.md');sections=['| Composer Command | Type | Action |\n| --- | --- | --- |\n| `/nuu audit [target]` | Agent note | Run a full `nuu` audit and publish the report |\n| `/nuu help` | Modal | Show `nuu` composer commands and clauses |\n| `/nuu learn` | Agent note | Survey repo UI/UX practice `nuu` could adopt |\n| `/nuu recipe <name> [target]` | Agent note | Apply a `nuu` recipe to the target |\n| `/nuu recipes` | Modal | List available `nuu` recipes and summaries |\n| `/nuu search <query>` | Modal | Search the `nuu` UI/UX catalog |']
			if clauses:sections.append(f"### Clauses\n\n{clauses}")
			return{'mode':'query','command':'/nuu help','title':'nuu · Quick Reference','markdown':'\n\n'.join(sections)}
		if sub=='recipes'or sub=='recipe'and not arg:
			recipes=_collect_nuu_recipes(nuu_dir);rows=['| Recipe | Summary | Composer Command |','| --- | --- | --- |']
			for(name,content)in sorted(recipes.items()):rows.append(f"| `{name}` | {_recipe_summary_line(content)} | `/nuu recipe {name} [target]` |")
			return{'mode':'query','command':'/nuu recipes','title':'nuu · Recipes','markdown':'\n'.join(rows)}
		if sub=='search':
			if not arg:return{'mode':'query','command':'/nuu search','title':'nuu search','markdown':'Usage: `/nuu search <query>`'}
			try:tokens=shlex.split(arg)
			except ValueError as err:return{'mode':'query','command':'/nuu search','title':'nuu search','markdown':f"Invalid search arguments: `{err}`"}
			proc=subprocess.run([sys.executable,str(nuu_dir/'scripts'/'uiux.py'),'search',*tokens],cwd=str(repo_root),capture_output=True,text=True,timeout=15,check=False);out=(proc.stdout or proc.stderr or'No results.').strip();return{'mode':'query','command':f"/nuu search {arg}",'title':f"nuu search · {arg}",'markdown':f"```text\n{out}\n```"}
		if sub=='audit':
			bp_list=', '.join(f"`{bp}`"for bp in _extract_nuu_breakpoints(nuu_dir));nuu_rel=_skill_rel_path(nuu_dir,repo_root,'nuu');note_text=f"Run a full `nuu` UI/UX audit (breakpoints {bp_list}; `a11y`, `density`, and `annotate`; report shots for `1920x957` desktop maximized and `411x799` phone portrait only) and publish the audit report using `{nuu_rel}/references/audit-report.md`."
			if trailing:note_text=f"{note_text}\n\n{trailing}"
			return{'mode':'note','command':'/nuu audit','text':note_text}
		if sub=='learn':
			note_text='Survey this repository for UI/UX practice that `nuu` could adopt, naming the clause or recipe each candidate would extend, and publish a maintainer handoff.'
			if trailing:note_text=f"{note_text}\n\n{trailing}"
			return{'mode':'note','command':'/nuu learn','text':note_text}
		if sub=='recipe':
			recipe_parts=arg.split(None,1);recipe_name=re.sub('[^a-zA-Z0-9_-]','',recipe_parts[0]).lower();recipe_rest=recipe_parts[1].strip()if len(recipe_parts[1:])else'';recipe_trailing='\n\n'.join(part for part in(recipe_rest,extra_lines)if part).strip();nuu_rel=_skill_rel_path(nuu_dir,repo_root,'nuu');recipe_rel=f"{nuu_rel}/references/recipes/{recipe_name}.md";note_text=f"Apply the `nuu` recipe `{recipe_name}` (`{recipe_rel}`)."
			if recipe_trailing:note_text=f"{note_text}\n\n{recipe_trailing}"
			return{'mode':'note','command':f"/nuu recipe {recipe_name}",'text':note_text}
		return{'mode':'query','command':f"/nuu {sub}",'title':'nuu · Unknown Command','markdown':f"Unknown `/nuu` subcommand `{sub}`. Try `/nuu help`, `/nuu learn`, `/nuu recipes`, `/nuu search <query>`, `/nuu audit [target]`, or `/nuu recipe <name> [target]`."}
	nemo_dir=_find_skill_dir(repo_root,'nemoizer')
	if sub in{'','help'}:
		clauses=_extract_clauses(nemo_dir/'SKILL.md');sections=['| Composer Command | Type | Action |\n| --- | --- | --- |\n| `/nemoizer audit [paths...]` | Agent note | Run a `nemoizer` audit, fix violations, and publish the report |\n| `/nemoizer collect [paths...]` | Modal | Scan prose and code and summarize `nemoizer` findings |\n| `/nemoizer help` | Modal | Show `nemoizer` composer commands and clauses |\n| `/nemoizer learn` | Agent note | Survey repo wording and lint practice `nemoizer` could adopt |\n| `/nemoizer recipes` | Modal | List `nemoizer` workflow references and templates |\n| `/nemoizer rules [id]` | Modal | List STE, NMO, NMP, and NMT rules or view a rule summary |']
		if clauses:sections.append(f"### Clauses\n\n{clauses}")
		return{'mode':'query','command':'/nemoizer help','title':'nemoizer · Quick Reference','markdown':'\n\n'.join(sections)}
	if sub in{'recipes','recipe'}:return{'mode':'query','command':'/nemoizer recipes','title':'nemoizer · Templates & Workflows','markdown':'| Reference | Purpose | Composer Command |\n| --- | --- | --- |\n| `references/audit-report.md` | Standalone audit report template | `/nemoizer audit [paths...]` |\n| `references/rules.md` | Index of STE, NMO, NMP, and NMT rules | `/nemoizer rules [id]` |\n| Audit findings scan | Group suppressions, violations, and review items | `/nemoizer collect [paths...]` |'}
	if sub in{'rules','rule'}:
		refs_dir=nemo_dir/'references'
		if arg and refs_dir.is_dir():
			slug=re.sub('[^a-zA-Z0-9_-]','',arg.split()[0]).lower()
			for item in sorted(refs_dir.glob('*.md')):
				if item.stem.lower()==slug:return{'mode':'query','command':f"/nemoizer {sub} {slug}",'title':f"nemoizer · {slug.upper()}",'markdown':strip_fenced_md_comments(_concise_rule_doc(item.read_text(encoding='utf-8'))).strip()}
		rules_file=refs_dir/'rules.md'if(refs_dir/'rules.md').is_file()else refs_dir/'Rules.md';body=strip_fenced_md_comments(rules_file.read_text(encoding='utf-8')).strip()if rules_file.is_file()else'No rules file found.';return{'mode':'query','command':f"/nemoizer {sub}",'title':'nemoizer · Rules','markdown':body}
	if sub in{'collect','check'}:
		target_args=[]
		if arg:
			try:
				for token in shlex.split(arg):
					if token.startswith('-'):continue
					resolved=(repo_root/token).resolve()
					if resolved==repo_root or repo_root in resolved.parents:target_args.append(str(resolved.relative_to(repo_root)))
			except ValueError:pass
		ignore_args=[]
		if not target_args:
			for item in NEMOIZER_COLLECT_IGNORES:ignore_args.extend(['--ignore',item])
		try:proc=subprocess.run([sys.executable,str(nemo_dir/'scripts'/'nemoize.py'),'--audit',*ignore_args,*target_args],cwd=str(repo_root),capture_output=True,text=True,timeout=60,check=False);out=(proc.stdout or proc.stderr or'').strip()
		except subprocess.TimeoutExpired:return{'mode':'query','command':f"/nemoizer {sub}",'title':'nemoizer collect · Timeout','markdown':'Scan timed out. Pass a narrower path (e.g. `/nemoizer collect docs/ README.md`).'}
		finding_lines=[line for line in out.splitlines()if line.strip()];summary=f"Found **{len(finding_lines)}** output line(s).\n\n```text\n{out}\n```"if finding_lines else'No findings.';return{'mode':'query','command':f"/nemoizer {sub}"+(f" {arg}"if arg else''),'title':'nemoizer collect · Audit Findings','markdown':summary}
	if sub=='audit':
		nemo_rel=_skill_rel_path(nemo_dir,repo_root,'nemoizer');note_text=f"Run `nemoizer` in audit mode (`python3 {nemo_rel}/scripts/nemoize.py --audit`), fix blocking violations, and publish an audit report using `{nemo_rel}/references/audit-report.md`."
		if trailing:note_text=f"{note_text}\n\n{trailing}"
		return{'mode':'note','command':'/nemoizer audit','text':note_text}
	if sub=='learn':
		note_text="Survey this repository's prose and code comments for wording or lint practice that `nemoizer` could adopt, naming the rule or reference each candidate would extend, and publish a maintainer handoff."
		if trailing:note_text=f"{note_text}\n\n{trailing}"
		return{'mode':'note','command':'/nemoizer learn','text':note_text}
	return{'mode':'query','command':f"/nemoizer {sub}",'title':'nemoizer · Unknown Command','markdown':f"Unknown `/nemoizer` subcommand `{sub}`. Try `/nemoizer help`, `/nemoizer learn`, `/nemoizer rules [id]`, `/nemoizer recipes`, `/nemoizer collect [paths...]`, or `/nemoizer audit [paths...]`."}
class ReportChanged(ValueError):pass
class FetchChanged(ValueError):pass
class ReportHeld(ValueError):pass
def format_report_state(rows):
	lines=[]
	for row in rows:
		lines.append(f"{row['id']} {row['title']}");lines.append(f"  published: {local_stamp(row['published_at'])or'never'}");lines.append(f"  updated: {local_stamp(row['updated_at'])}");lines.append(f"  stale: {'yes'if row['stale']else'no'}");lines.append(f"  pinned: {'yes'if row.get('pinned')else'no'}");lines.append(f"  viewed: {local_stamp(row['viewed_at'])or'never'}")
		if not row['answers']:lines.append('  answers: none')
		for answer in row['answers']:lines.append(f"  answers: {answer['id']} acknowledged {'yes'if answer['acknowledged']else'no'}, agent-seen {'yes'if answer['seen']else'no'}")
	return'\n'.join(lines)+('\n'if lines else'')
def sqlite_api():
	global sqlite3
	if sqlite3 is None:import sqlite3
	return sqlite3
class Store:
	def __init__(self,directory,create=False,save_path=None):
		directory=Path(directory).resolve()
		if directory.name=='state.sqlite3':directory=directory.parent
		self.path=directory/'state.sqlite3';self.save_path=Path(save_path)if save_path else self.path.parent/SAVED_STATE;self.latest_backup_path=self.path.parent/BACKUP_DIR/LATEST_BACKUP;self.previous_backup_path=self.path.parent/BACKUP_DIR/PREVIOUS_BACKUP;existed=self.path.is_file()
		if not create and not existed:raise FileNotFoundError(f"Inbox missing: {self.path}. The sandbox may have been reset, so follow the restore routine: run scripts/install.sh from the repository root, start `arena-preview serve --port 8000` with the start_process tool, then `arena-preview read`.")
		if create and not existed:directory.mkdir(parents=True,exist_ok=True,mode=448)
		with closing(self.connect())as db,db:
			db.executescript("\n        CREATE TABLE IF NOT EXISTS notes (\n          seq INTEGER PRIMARY KEY, id TEXT UNIQUE NOT NULL,\n          text TEXT NOT NULL, at TEXT NOT NULL, acknowledged_at TEXT,\n          ack_type TEXT, ack_text TEXT, seen_at TEXT, replies TEXT,\n          ack_edited_seen_count INTEGER NOT NULL DEFAULT 0,\n          quiet INTEGER NOT NULL DEFAULT 0,\n          blocking INTEGER NOT NULL DEFAULT 1,\n          type TEXT NOT NULL DEFAULT ''\n        );\n        CREATE TABLE IF NOT EXISTS reports (\n          id TEXT PRIMARY KEY, title TEXT NOT NULL,\n          markdown TEXT NOT NULL, updated_at TEXT NOT NULL, published_at TEXT,\n          seq INTEGER, seen_at TEXT,\n          ever_seen INTEGER NOT NULL DEFAULT 0, agent_seen_at TEXT,\n          ack_seen_at TEXT, viewed_at TEXT\n        );\n        CREATE TABLE IF NOT EXISTS submissions (\n          seq INTEGER PRIMARY KEY, id TEXT UNIQUE NOT NULL,\n          report_id TEXT NOT NULL, text TEXT NOT NULL, at TEXT NOT NULL,\n          acknowledged_at TEXT, ack_type TEXT, ack_text TEXT, seen_at TEXT, replies TEXT,\n          ack_edited_seen_count INTEGER NOT NULL DEFAULT 0\n        );\n        CREATE TABLE IF NOT EXISTS uploads (\n          seq INTEGER PRIMARY KEY, id TEXT UNIQUE NOT NULL, note_id TEXT,\n          name TEXT NOT NULL, type TEXT NOT NULL, size INTEGER NOT NULL,\n          sha256 TEXT NOT NULL, file TEXT NOT NULL, at TEXT NOT NULL\n        );\n        CREATE TABLE IF NOT EXISTS fetch_jobs (\n          seq INTEGER PRIMARY KEY, id TEXT UNIQUE NOT NULL,\n          url TEXT NOT NULL, allow_proxy INTEGER NOT NULL CHECK (allow_proxy IN (0, 1)),\n          status TEXT NOT NULL CHECK (status IN ('queued', 'fetching', 'saved', 'failed')),\n          approval TEXT NOT NULL DEFAULT 'approved'\n            CHECK (approval IN ('pending', 'approved', 'denied')),\n          claim TEXT, lease_until TEXT, error TEXT, source TEXT,\n          name TEXT, type TEXT, size INTEGER, sha256 TEXT, file TEXT,\n          at TEXT NOT NULL, updated_at TEXT NOT NULL\n        );\n        CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);\n        CREATE TABLE IF NOT EXISTS task_dependencies (\n          task_id TEXT NOT NULL, blocked_by TEXT NOT NULL,\n          PRIMARY KEY (task_id, blocked_by)\n        );\n        CREATE TABLE IF NOT EXISTS tasks (\n          id TEXT PRIMARY KEY,\n          title TEXT NOT NULL,\n          details TEXT NOT NULL DEFAULT '[]',\n          status TEXT NOT NULL DEFAULT 'upcoming'\n            CHECK (status IN ('upcoming', 'finished')),\n          position INTEGER NOT NULL,\n          created_at TEXT NOT NULL,\n          updated_at TEXT NOT NULL,\n          completed_at TEXT,\n          blocked INTEGER NOT NULL DEFAULT 0,\n          type TEXT NOT NULL DEFAULT 'misc'\n        );\n      ");columns={row['name']for row in db.execute('PRAGMA table_info(notes)')}
			for column in('ack_type','ack_text','ack_edited_at','seen_at','task_id','replies'):
				if column not in columns:db.execute(f"ALTER TABLE notes ADD COLUMN {column} TEXT")
			if'ack_edited_seen_count'not in columns:db.execute('ALTER TABLE notes ADD COLUMN ack_edited_seen_count INTEGER NOT NULL DEFAULT 0')
			if'quiet'not in columns:db.execute('ALTER TABLE notes ADD COLUMN quiet INTEGER NOT NULL DEFAULT 0')
			if'blocking'not in columns:db.execute('ALTER TABLE notes ADD COLUMN blocking INTEGER NOT NULL DEFAULT 1');db.execute('UPDATE notes SET blocking = CASE WHEN quiet = 1 THEN 0 ELSE 1 END')
			if'type'not in columns:db.execute("ALTER TABLE notes ADD COLUMN type TEXT NOT NULL DEFAULT ''")
			if'origin'in columns:db.execute('ALTER TABLE notes DROP COLUMN origin');columns.discard('origin')
			if'seen_at'not in columns:db.execute('UPDATE notes SET seen_at = acknowledged_at WHERE seen_at IS NULL AND acknowledged_at IS NOT NULL')
			columns={row['name']for row in db.execute('PRAGMA table_info(tasks)')}
			if'blocked'not in columns:db.execute('ALTER TABLE tasks ADD COLUMN blocked INTEGER NOT NULL DEFAULT 0')
			if'report_id'not in columns:db.execute('ALTER TABLE tasks ADD COLUMN report_id TEXT')
			if'completed_at'not in columns:db.execute('ALTER TABLE tasks ADD COLUMN completed_at TEXT')
			if'type'not in columns:db.execute("ALTER TABLE tasks ADD COLUMN type TEXT NOT NULL DEFAULT 'misc'")
			db.execute("UPDATE tasks SET completed_at = updated_at WHERE status = 'finished' AND completed_at IS NULL");columns={row['name']for row in db.execute('PRAGMA table_info(submissions)')}
			if'ack_edited_at'not in columns:db.execute('ALTER TABLE submissions ADD COLUMN ack_edited_at TEXT')
			if'seen_at'not in columns:db.execute('ALTER TABLE submissions ADD COLUMN seen_at TEXT')
			if'task_id'not in columns:db.execute('ALTER TABLE submissions ADD COLUMN task_id TEXT')
			if'replies'not in columns:db.execute('ALTER TABLE submissions ADD COLUMN replies TEXT')
			if'ack_edited_seen_count'not in columns:db.execute('ALTER TABLE submissions ADD COLUMN ack_edited_seen_count INTEGER NOT NULL DEFAULT 0')
			submission_columns={row['name']for row in db.execute('PRAGMA table_info(submissions)')}
			if'answer_data'not in submission_columns:db.execute('ALTER TABLE submissions ADD COLUMN answer_data TEXT')
			columns={row['name']for row in db.execute('PRAGMA table_info(reports)')}
			if'seen_at'not in columns:db.execute('ALTER TABLE reports ADD COLUMN seen_at TEXT');db.execute('UPDATE submissions SET seen_at = acknowledged_at WHERE seen_at IS NULL AND acknowledged_at IS NOT NULL')
			columns={row['name']for row in db.execute('PRAGMA table_info(reports)')}
			if'source'not in columns:db.execute('ALTER TABLE reports ADD COLUMN source TEXT')
			if'seq'not in columns:db.execute('ALTER TABLE reports ADD COLUMN seq INTEGER');db.execute('UPDATE reports SET seq = rowid WHERE seq IS NULL')
			if'ever_seen'not in columns:db.execute('ALTER TABLE reports ADD COLUMN ever_seen INTEGER NOT NULL DEFAULT 0');db.execute('UPDATE reports SET ever_seen = 1 WHERE seen_at IS NOT NULL')
			if'agent_seen_at'not in columns:db.execute('ALTER TABLE reports ADD COLUMN agent_seen_at TEXT')
			if'ack_seen_at'not in columns:db.execute('ALTER TABLE reports ADD COLUMN ack_seen_at TEXT')
			if'viewed_at'not in columns:db.execute('ALTER TABLE reports ADD COLUMN viewed_at TEXT')
			columns={row['name']for row in db.execute('PRAGMA table_info(reports)')}
			if'submission_format'not in columns:db.execute('ALTER TABLE reports ADD COLUMN submission_format TEXT')
			if'last_viewed_at'not in columns:db.execute('ALTER TABLE reports ADD COLUMN last_viewed_at TEXT')
			if'pinned'not in columns:db.execute('ALTER TABLE reports ADD COLUMN pinned INTEGER NOT NULL DEFAULT 0')
			if'published_at'not in columns:db.execute('ALTER TABLE reports ADD COLUMN published_at TEXT');db.execute('UPDATE reports SET published_at = updated_at WHERE published_at IS NULL')
			columns={row['name']for row in db.execute('PRAGMA table_info(uploads)')}
			if'note_id'not in columns:db.execute('ALTER TABLE uploads ADD COLUMN note_id TEXT');db.execute('UPDATE uploads SET note_id = id WHERE note_id IS NULL')
			db.execute('CREATE INDEX IF NOT EXISTS uploads_note_id ON uploads(note_id)');columns={row['name']for row in db.execute('PRAGMA table_info(fetch_jobs)')}
			if'approval'not in columns:db.execute("ALTER TABLE fetch_jobs ADD COLUMN approval TEXT NOT NULL DEFAULT 'approved' CHECK (approval IN ('pending', 'approved', 'denied'))")
			if'origin'not in columns:db.execute("ALTER TABLE fetch_jobs ADD COLUMN origin TEXT NOT NULL DEFAULT 'agent'")
		if not existed:self.path.chmod(384)
		if create:self._auto_restore_if_empty()
	def _auto_restore_if_empty(self):
		if self.meta_value(CLEARED_META)=='1':return
		with closing(self.connect())as db:has_rows=any(db.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone()for table in('notes','reports','submissions','tasks'))
		if has_rows:return
		for candidate in(self.latest_backup_path,self.previous_backup_path):
			if not candidate.is_file():continue
			try:text=candidate.read_text(encoding='utf-8')
			except OSError:continue
			if not text.strip():continue
			try:self.import_state(text,replace_tasks=True);return
			except(OSError,ValueError,TypeError,KeyError,sqlite_api().Error,RuntimeError)as error:print(f"preview: auto-restore skipped {candidate.name}: {error}",file=sys.stderr)
	def id_matches(self,token):
		token=bare_id(token);like=f"{token}%";found=[]
		with closing(self.connect())as db:
			for(table,label)in(('notes','note'),('submissions','answer'),('reports','report'),('tasks','task')):rows=db.execute(f"SELECT * FROM {table} WHERE id = ? OR id LIKE ?",(token,like)).fetchall();found.extend((label,dict(row))for row in rows)
			b_row=db.execute('SELECT value FROM meta WHERE key = ?',(BOARD_STATE_META,)).fetchone()
			if b_row and b_row[0]:
				try:b_raw=json.loads(str(b_row[0]))
				except Exception:b_raw=None
				if isinstance(b_raw,dict):
					if isinstance(b_raw.get('entries'),list):
						existing_ids={str(r.get('id')or'')for(_,r)in found}
						for entry in b_raw['entries']:
							if not isinstance(entry,dict):continue
							eid=str(entry.get('id')or'')
							if not eid or eid in existing_ids:continue
							alt_id=f"p{eid[2:7]}"if re.match('^b-[0-9a-f]{5,}',eid,re.IGNORECASE)else''
							if eid==token or eid.startswith(token)or alt_id and(alt_id==token or alt_id.startswith(token)):existing_ids.add(eid);found.append(('note',{'id':eid,'at':str(entry.get('at')or'')}))
					for(m_id,m_rep)in self._mirrored_worker_reports().items():
						if(m_id==token or m_id.startswith(token))and not any(r.get('id')==m_id for(_,r)in found):found.append(('report',dict(m_rep)))
		return found
	def resolve_id(self,token):
		token=bare_id(token);matches=self.id_matches(token);exact=[item for item in matches if item[1]['id']==token]
		if len(exact)==1:return token
		if len(matches)==1:return matches[0][1]['id']
		if not matches:raise ValueError(f"No note, answer, report or task holds the ID {token}")
		listed='; '.join(f"{type} {record['id']} {(record.get('title')or record.get('text')or'')[:40]!r}"for(type,record)in matches);raise ValueError(f"{token} matches {len(matches)} records: {listed}. Give the full ID of one")
	def list_note_ids(self,page,page_size):
		if page<1 or not 1<=page_size<=100:raise ValueError('Page must be at least 1; page size must be 1 to 100')
		with closing(self.connect())as db:
			db.execute('BEGIN');total=db.execute('SELECT count(*) FROM notes').fetchone()[0];offset=(page-1)*page_size;ids=[]
			if offset<total:ids=[row[0]for row in db.execute('SELECT id FROM notes ORDER BY at DESC, seq DESC LIMIT ? OFFSET ?',(page_size,offset))]
		return ids,total
	def notes_matching(self,token):
		with closing(self.connect())as db:matches=[message_row(row)for row in db.execute('SELECT * FROM notes WHERE id = ? OR id LIKE ? ORDER BY at DESC',(token,f"{token}%")).fetchall()];b_row=db.execute('SELECT value FROM meta WHERE key = ?',(BOARD_STATE_META,)).fetchone()
		if b_row and b_row[0]:
			try:b_raw=json.loads(str(b_row[0]))
			except Exception:b_raw=None
			if isinstance(b_raw,dict):
				sid=board_branch(b_raw.get('local_branch')or b_raw.get('local_session_id')or workspace_branch(self.repository_root()));norm=normalize_board_state(b_raw,branch=sid)
				if not sid or sid in set(norm.get('allowed_sessions')or[]):
					seen_ids={str(m.get('id')or'')for m in matches}
					for entry in reversed(norm.get('entries')or[]):
						if not isinstance(entry,dict):continue
						eid=str(entry.get('id')or'');nid=str(entry.get('note_id')or'')
						if eid and eid not in seen_ids and(eid==token or eid.startswith(token)or nid and(nid==token or nid.startswith(token))):seen_ids.add(eid);matches.append({'id':eid,'at':str(entry.get('at')or now()),'acknowledged_at':entry.get('acknowledged_at'),'text':str(entry.get('text')or''),'patch_hint':board_patch_apply_hint(entry,state_dir=self.path.parent,repo_root=self.repository_root())})
		return matches
	def connect(self):db=sqlite_api().connect(self.path,timeout=5);db.row_factory=sqlite_api().Row;return db
	def note(self,note_id,text,at=None,acknowledged_at=None,ack_type=None,ack_text=None,seen_at=None,task_id=None,ack_edited_at=None,autosave=True,shared=None,replies=None,ack_edited_seen_count=None,quiet=False,blocking=None,type=''):
		identifier(note_id);owner_text(text);receipt=restore_receipt(acknowledged_at,ack_type,ack_text,ack_edited_at);more=restore_replies(replies,receipt[0]);seen_reply_count=restore_reply_seen_count(ack_edited_seen_count,more);seen=when(seen_at)if seen_at is not None else None;blocking=not quiet if blocking is None else bool(blocking)
		with self.transaction(shared,autosave=autosave)as db:
			if shared is None:db.execute('BEGIN IMMEDIATE')
			existing=db.execute('SELECT * FROM notes WHERE id = ?',(note_id,)).fetchone()
			if existing:
				if existing['text']!=text:raise ValueError('This message ID already belongs to different text')
				return message_row(existing)
			db.execute('INSERT INTO notes (id, text, at, acknowledged_at, ack_type, ack_text, ack_edited_at, seen_at, task_id, replies, ack_edited_seen_count, quiet, blocking, type) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',(note_id,text,at or now(),*receipt,seen,task_id,more,seen_reply_count,1 if quiet else 0,1 if blocking else 0,type))
			if not quiet or blocking:reset_poll_count(db)
			clear_skip_poll(db);return message_row(db.execute('SELECT * FROM notes WHERE id = ?',(note_id,)).fetchone())
	def pending_read_set_note(self):
		with closing(self.connect())as db:
			flag=db.execute('SELECT value FROM meta WHERE key = ?',(AGENT_INTERRUPT_FLAG_META,)).fetchone()
			try:flag_id=json.loads(flag['value']).get('id','')if flag else''
			except ValueError:flag_id=''
			row=db.execute("SELECT * FROM notes WHERE quiet = 1 AND blocking = 1 AND type = '' AND acknowledged_at IS NULL AND id <> ? ORDER BY at, seq LIMIT 1",(flag_id,)).fetchone()
		return message_row(row)if row else None
	def pending_violation_note(self):
		with closing(self.connect())as db:row=db.execute(f"SELECT * FROM notes WHERE {VIOLATION_NOTES} AND acknowledged_at IS NULL ORDER BY at, seq LIMIT 1").fetchone()
		return message_row(row)if row else None
	def schedule_read_set_note(self,moment=None,root=None,manual=False):
		moment=moment or datetime.now(DISPLAY_ZONE or UTC)
		if moment.tzinfo is None:moment=moment.replace(tzinfo=UTC)
		slot=read_set_slot(moment);is_worker=self.board_state().get('board_role')=='worker';paths=read_set_paths(root or self.repository_root(),worker=is_worker)
		with closing(self.connect())as db,db:
			db.execute('BEGIN IMMEDIATE')
			if not manual:
				heartbeat=db.execute('SELECT value FROM meta WHERE key = ?',(POLLING_META,)).fetchone();age=seconds_since(heartbeat['value']if heartbeat else None)
				if age is not None and 0<=age<POLLING_FRESH_SECONDS:return None
			row=db.execute('SELECT * FROM notes WHERE quiet = 1 AND blocking = 1 AND acknowledged_at IS NULL ORDER BY at, seq LIMIT 1').fetchone()
			if row:return message_row(row)
			if not manual:
				recorded=db.execute('SELECT value FROM meta WHERE key = ?',(READ_SET_LAST_META,)).fetchone()
				if recorded is None:recorded=db.execute('SELECT value FROM meta WHERE key = ?',(READ_SET_SLOT_META,)).fetchone()
				try:previous=datetime.fromisoformat(recorded['value'])if recorded else None
				except ValueError:previous=None
				if previous is None:db.execute('INSERT OR REPLACE INTO meta VALUES (?, ?)',(READ_SET_LAST_META,moment.isoformat()));return None
				if previous.tzinfo is None:previous=previous.replace(tzinfo=UTC)
				if(moment-previous).total_seconds()<READ_SET_INTERVAL_MINUTES*60:return None
			for _ in range(MINT_ATTEMPTS):
				note_id=new_id()
				if not any(db.execute(f"SELECT 1 FROM {table} WHERE id = ?",(note_id,)).fetchone()for table in('notes','submissions','uploads','reports','tasks')):break
			else:raise RuntimeError('The state holds no free six-character ID for a bible note')
			listed='\n'.join(f"- `{path}`"for path in paths);prefix='Bible checkpoint. 'if manual else'15min bible check\n';import survey;text=f"{prefix}Read each listed path fully with the read tool only when its full text is absent from context, then acknowledge this note.\nAnswer the survey only when required bible text was absent before this check.\n{survey.INSTRUCTIONS}\n{listed}";record=self.note(note_id,text,shared=db,autosave=False,quiet=True,blocking=True);db.execute('INSERT INTO meta(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value',(READ_SET_SLOT_META,slot));db.execute('INSERT INTO meta(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value',(READ_SET_LAST_META,moment.isoformat()))
		self.autosave();return record
	def submission(self,submission_id,report_id,text,at=None,acknowledged_at=None,ack_type=None,ack_text=None,seen_at=None,task_id=None,ack_edited_at=None,shared=None,autosave=True,replies=None,ack_edited_seen_count=None,answer_data=None):
		identifier(submission_id);identifier(report_id);submission_text(text)
		if answer_data is not None:
			if not isinstance(answer_data,dict)or set(answer_data)!={'revision','answers'}or not isinstance(answer_data['revision'],str)or not isinstance(answer_data['answers'],dict):raise ValueError('Saved answer data requires a revision and raw answers')
			when(answer_data['revision'])
			if len(json.dumps(answer_data,ensure_ascii=False).encode('utf-8'))>MAX_REPORT:raise ValueError('Saved answer data exceeds the report size limit')
		receipt=restore_receipt(acknowledged_at,ack_type,ack_text,ack_edited_at);more=restore_replies(replies,receipt[0]);seen_reply_count=restore_reply_seen_count(ack_edited_seen_count,more)
		with self.transaction(shared,autosave=autosave)as db:
			if shared is None:db.execute('BEGIN IMMEDIATE')
			existing=db.execute('SELECT * FROM submissions WHERE id = ?',(submission_id,)).fetchone()
			if existing:
				if existing['text']!=text or answer_data is not None and json.loads(existing['answer_data']or'null')!=answer_data:raise ValueError('This message ID already belongs to different text')
				self.release_report_tasks(db,report_id);return message_row(existing)
			if at is None and acknowledged_at is None:
				latest=db.execute('SELECT * FROM submissions WHERE report_id = ? ORDER BY seq DESC LIMIT 1',(report_id,)).fetchone()
				if latest is not None and latest['text']==text and(answer_data is None or latest['answer_data']is None or json.loads(latest['answer_data']or'null')==answer_data):self.release_report_tasks(db,report_id);return message_row(latest)
			db.execute('INSERT INTO submissions (id, report_id, text, at, acknowledged_at, ack_type, ack_text, ack_edited_at, seen_at, task_id, replies, ack_edited_seen_count, answer_data) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',(submission_id,report_id,text,at or now(),*receipt,when(seen_at)if seen_at is not None else None,task_id,more,seen_reply_count,json.dumps(answer_data,ensure_ascii=False)if answer_data is not None else None));self.release_report_tasks(db,report_id);reset_poll_count(db);clear_skip_poll(db);return message_row(db.execute('SELECT * FROM submissions WHERE id = ?',(submission_id,)).fetchone())
	def submissions(self):
		with closing(self.connect())as db:return[message_row(row)for row in db.execute('SELECT * FROM submissions ORDER BY at, seq')]
	def report_sources(self):
		with closing(self.connect())as db:return[dict(row,pinned=bool(row['pinned']),submission_format=json.loads(row['submission_format'])if row['submission_format']else None)for row in db.execute('SELECT id, title, markdown, published_at, seen_at, ack_seen_at, pinned, submission_format, source FROM reports ORDER BY seq, id')]
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
			polling=self.polling();runs=live_runs(self.path.parent)if polling else[];board_now=self.board_state(meta=meta,polling=polling);mirrored_reports=self._mirrored_worker_reports(board_now=board_now,local_ids={r['id']for r in reports},meta=meta)
			if mirrored_reports:
				for m_rep in mirrored_reports.values():m_copy=dict(m_rep);m_copy.pop('markdown',None);reports.append(m_copy)
			if isinstance(board_now.get('worker_reports'),dict):board_now['worker_reports']={w_sid:[{k:v for(k,v)in w_r.items()if k not in{'markdown','fields'}}for w_r in w_list if isinstance(w_r,dict)]for(w_sid,w_list)in board_now['worker_reports'].items()if isinstance(w_list,list)}
			if isinstance(board_now.get('entries'),list):board_now['entries']=[{**e_row,'file':{fk:fv for(fk,fv)in e_row['file'].items()if fk!='content'}if isinstance(e_row.get('file'),dict)else e_row.get('file')}for e_row in board_now['entries']if isinstance(e_row,dict)]
			return{'notes':notes,'reports':reports,'tasks':tasks,'uploads':uploads,'fetch_jobs':fetch_jobs,'workspace':workspace_usage(state_dir=self.path.parent),'last_check':clip_stamp(meta.get('last_check')),'polling':polling,'waiting_for_gates':bool(runs),'checking_pr':any(pr_checks_command(routed_record(self.path.parent,name).get('argv',[]))for name in runs),'polling_since':clip_stamp(meta.get(POLL_SINCE_META))if polling else None,'skip_poll':clip_stamp(meta.get(SKIP_POLL_META)),'agent_interrupt_pending':bool(meta.get(AGENT_INTERRUPT_FLAG_META)),'read_set':read_set_files(self.repository_root(),worker=board_now.get('board_role')=='worker'),'calls_since_message':meta_number(db,POLLS_SINCE_MESSAGE),'agent_key':self.agent_key(),'egress_version':self.egress_version(),'userscript_version':self.userscript_version(),'agent_seen_at':clip_stamp(meta.get(AGENT_SEEN_META)),'last_tool':tool_activity(meta),'turn_ended_at':clip_stamp(meta.get(TURN_ENDED_META)),'agent_call_ended_at':clip_stamp(meta.get(AGENT_CALL_ENDED_META)),'gate_threshold':GATE_THRESHOLD,'display_tz':meta.get(TIMEZONE_META),'repo':self.repository_root().name,'force_save_id':meta.get(FORCE_SAVE_META),'board':board_now}
	def _mirrored_worker_reports(self,board_now=None,local_ids=None,meta=None):del board_now,local_ids,meta;return{}
	def _forwarded_worker_reports(self):
		raw_text=self.meta_value(BOARD_STATE_META)
		try:stored=json.loads(raw_text)if raw_text else{}
		except Exception:stored={}
		ack_wakes=stored.get('ack_wakes')if isinstance(stored,dict)and isinstance(stored.get('ack_wakes'),dict)else{};mapping=ack_wakes.get('forwarded_worker_reports');return dict(mapping)if isinstance(mapping,dict)else{}
	def _record_forwarded_worker_report(self,local_id,worker_branch,worker_report_id):
		with self.transaction()as db:
			row=db.execute('SELECT value FROM meta WHERE key = ?',(BOARD_STATE_META,)).fetchone()
			try:stored=json.loads(row[0])if row and row[0]else{}
			except Exception:stored={}
			if not isinstance(stored,dict):stored={}
			ack_wakes=dict(stored.get('ack_wakes'))if isinstance(stored.get('ack_wakes'),dict)else{};fwd=dict(ack_wakes.get('forwarded_worker_reports'))if isinstance(ack_wakes.get('forwarded_worker_reports'),dict)else{};fwd[local_id]={'worker_branch':worker_branch,'worker_report_id':worker_report_id};ack_wakes['forwarded_worker_reports']=fwd;stored['ack_wakes']=ack_wakes;db.execute('INSERT OR REPLACE INTO meta VALUES (?, ?)',(BOARD_STATE_META,json.dumps(stored)))
	def _locate_worker_report_source(self,worker_target,report_id):
		identifier(report_id);b_state=self.board_state()
		if b_state.get('board_role')=='worker':raise ValueError('Only the main session can triage worker reports')
		worker_br=resolve_board_target(worker_target,b_state.get('sessions')or(),allowed=b_state.get('allowed_sessions')or(),main_sid=b_state.get('main_session_id')or'')
		if not worker_br:raise ValueError('Missing worker target')
		state_dir=self.path.parent;raw_text=self.meta_value(BOARD_STATE_META)
		try:raw_board=json.loads(raw_text)if raw_text else{}
		except Exception:raw_board={}
		raw_entries=raw_board.get('entries')if isinstance(raw_board,dict)and isinstance(raw_board.get('entries'),list)else[];source_path=None;title=''
		for entry in reversed([*(b_state.get('entries')or[]),*raw_entries]):
			if not isinstance(entry,dict):continue
			entry_br=resolve_board_target(entry.get('branch')or entry.get('session_id')or'',b_state.get('sessions')or(),allowed=b_state.get('allowed_sessions')or(),main_sid=b_state.get('main_session_id')or'');payload=entry.get('payload')if isinstance(entry.get('payload'),dict)else{}
			if entry_br!=worker_br or str(payload.get('report_id')or'')!=report_id:continue
			if payload.get('report_title')and not title:title=str(payload['report_title'])
			file_obj=entry.get('file')if isinstance(entry.get('file'),dict)else None
			if file_obj is None:continue
			staged_rel=str(file_obj.get('staged_path')or'').strip();cand=state_dir/staged_rel if staged_rel else state_dir/'board-files'/encode_board_branch_dir(worker_br)/str(file_obj.get('name')or f"{report_id}.md")
			if not cand.is_file()and isinstance(file_obj.get('content'),str)and file_obj['content']:staged=stage_board_file(state_dir,worker_br,file_obj);cand=state_dir/staged['staged_path']
			if cand.is_file():source_path=cand;break
		raw_wr=raw_board.get('worker_reports')if isinstance(raw_board,dict)and isinstance(raw_board.get('worker_reports'),dict)else{};rep_meta=next((r for r in[*((b_state.get('worker_reports')or{}).get(worker_br)or[]),*(raw_wr.get(worker_br)or[])]if isinstance(r,dict)and str(r.get('id')or'')==report_id),None)
		if rep_meta and rep_meta.get('title')and not title:title=str(rep_meta['title'])
		if source_path is None and rep_meta and isinstance(rep_meta.get('markdown'),str):fallback_dir=state_dir/'board-files'/encode_board_branch_dir(worker_br);fallback_dir.mkdir(parents=True,exist_ok=True);source_path=fallback_dir/f"{report_id}.md";source_path.write_text(rep_meta['markdown'],encoding='utf-8')
		if source_path is None or not source_path.is_file():raise FileNotFoundError(f"Worker report {report_id!r} from {worker_br} has no staged source on board")
		return worker_br,source_path,title or report_id
	def answer_worker_report(self,worker_target,report_id,answers):
		worker_br,source_path,title=self._locate_worker_report_source(worker_target,report_id);md_text=source_path.read_text(encoding='utf-8');fields=parse_fields(md_text)[1]
		if not fields:raise ValueError('This worker report has no fields to answer')
		self._format_report_submission(report_id,title,fields,answers);self.board_action({'action':'worker-report','target':worker_br,'op':'answer','report_id':report_id,'answers':answers});return{'worker':worker_br,'report_id':report_id}
	def forward_worker_report(self,worker_target,report_id,*,local_id=None,title_override=None):worker_br,source_path,title=self._locate_worker_report_source(worker_target,report_id);dest_id=str(local_id or report_id).strip();dest_title=str(title_override or title or report_id).strip();count=self.publish(dest_id,dest_title,source_path);self._record_forwarded_worker_report(dest_id,worker_br,report_id);return{'id':dest_id,'worker':worker_br,'worker_report_id':report_id,'fields':count}
	def board_state(self,meta=None,polling=None,_scope_repos=True):
		root=self.repository_root();branch=workspace_branch(root);repo=root.name;sid=board_branch(branch);raw_text=meta.get(BOARD_STATE_META)if isinstance(meta,dict)else self.meta_value(BOARD_STATE_META)
		try:raw=json.loads(raw_text)if raw_text else{}
		except Exception:raw={}
		is_polling=self.polling()if polling is None else bool(polling);out=normalize_board_state(raw,repo=repo,branch=sid,polling=is_polling,scope_repos=_scope_repos);allowed_set=set(out.get('allowed_sessions')or[]);is_self_allowed=not sid or sid in allowed_set;out['worker_tasks']={k:v for(k,v)in(out.get('worker_tasks')or{}).items()if k in allowed_set}if is_self_allowed else{};out['worker_reports']={k:v for(k,v)in(out.get('worker_reports')or{}).items()if k in allowed_set}if is_self_allowed else{}
		if not is_self_allowed:out['entries']=[];out['wakes']={}
		if sid:
			try:
				survey_raw=meta.get('survey_state_json')if isinstance(meta,dict)else self.meta_value('survey_state_json');checked_at=board_stamp_or_none(meta.get('survey_checked_at')if isinstance(meta,dict)else self.meta_value('survey_checked_at'));s_state=json.loads(survey_raw)if survey_raw else{};s_results=s_state.get('results')if isinstance(s_state,dict)else None;s_ans_at=board_stamp_or_none(s_state.get('answered_at')if isinstance(s_state,dict)else None)
				if s_results is not None and checked_at and(not s_ans_at or(board_stamp_ms(checked_at)or 0)>(board_stamp_ms(s_ans_at)or 0)):s_ans_at=checked_at
				self_survey={'answered':isinstance(s_results,list),'answered_at':s_ans_at,'results':s_results if isinstance(s_results,list)else None};runs=live_runs(self.path.parent)if is_polling else[]
				with closing(self.connect())as db_conn:calls_count=meta_number(db_conn,POLLS_SINCE_MESSAGE)
				m_dict=meta if isinstance(meta,dict)else{}
				def _mval(key):return m_dict.get(key)if key in m_dict else self.meta_value(key)
				root_dir=self.repository_root();cur_branch=workspace_branch(root_dir);cur_remote_head=workspace_git_ref(root_dir,f"refs/remotes/origin/{cur_branch}")if cur_branch else'';cur_origin_main=workspace_git_ref(root_dir,'refs/remotes/origin/main');self_status_fields={'survey':board_survey(self_survey,sid in allowed_set),'polling_since':board_stamp_or_none(_mval(POLL_SINCE_META))if is_polling else None,'waiting_for_gates':bool(runs),'checking_pr':any(pr_checks_command(routed_record(self.path.parent,name).get('argv',[]))for name in runs),'calls_since_message':calls_count,'gate_threshold':GATE_THRESHOLD,'agent_seen_at':board_stamp_or_none(_mval(AGENT_SEEN_META)),'turn_ended_at':board_stamp_or_none(_mval(TURN_ENDED_META)),'agent_call_ended_at':board_stamp_or_none(_mval(AGENT_CALL_ENDED_META))}
				if cur_remote_head:self_status_fields['remote_head']=cur_remote_head
				if cur_origin_main:self_status_fields['origin_main_head']=cur_origin_main
				for s_row in out['sessions']:
					if s_row.get('branch')==sid:
						s_row.update(self_status_fields);stamp_candidates=[(ms,val)for val in(s_row.get('updated_at'),self_status_fields.get('polling_since'),self_status_fields.get('agent_seen_at'),self_status_fields.get('turn_ended_at'),self_status_fields.get('agent_call_ended_at'))if val and(ms:=board_stamp_ms(val))is not None]
						if stamp_candidates:s_row['updated_at']=max(stamp_candidates,key=lambda pair:pair[0])[1]
			except Exception:pass
			if is_self_allowed:
				try:out['worker_tasks'][sid]=[{'id':row['id'],'title':row['title'],'status':row['status'],'type':row.get('type')or'code','details':list(row.get('details')or[]),'order':row.get('order'),'report_id':row.get('report_id'),'updated_at':board_stamp_or_none(row.get('updated_at'))}for row in self.list_tasks()]
				except Exception:pass
			try:
				with closing(self.connect())as db:rep_rows=db.execute('SELECT id, title, markdown, updated_at, published_at, seen_at, ever_seen, agent_seen_at, ack_seen_at, EXISTS(SELECT 1 FROM submissions WHERE report_id = reports.id) AS answered, EXISTS(SELECT 1 FROM tasks JOIN notes ON notes.task_id = tasks.id WHERE tasks.report_id = reports.id AND notes.quiet = 0) AS owner_requested FROM reports WHERE published_at IS NOT NULL ORDER BY seq, id').fetchall();sub_rows=db.execute('SELECT * FROM submissions ORDER BY seq').fetchall()
				latest_by_rep={};acks_by_rep={}
				for s_row in sub_rows:
					r_key=s_row['report_id'];latest_by_rep[r_key]=s_row
					if s_row['acknowledged_at']is not None:ack_msg=message_row(s_row);ack_msg.pop('text',None);acks_by_rep.setdefault(r_key,[]).append(ack_msg)
				rep_list=[]
				for r_row in rep_rows:
					try:fields=parse_fields(r_row['markdown'])[1]
					except Exception:fields=[]
					answered_flag=bool(r_row['answered']);rid=r_row['id'];latest_sub=latest_by_rep.get(rid);sent_flag=any(e.get('branch')==sid and(rid in str(e.get('text')or'')or str((e.get('payload')or{}).get('report_id')or'')==rid)for e in out.get('entries')or[]if isinstance(e,dict));rep_list.append({'id':rid,'title':r_row['title'],'markdown':r_row['markdown'],'fields':fields,'answered':answered_flag,'needs_answer':bool(fields)and not answered_flag,'seen_at':board_stamp_or_none(r_row['seen_at']),'ever_seen':bool(r_row['ever_seen']),'latest_answer_id':latest_sub['id']if latest_sub else None,'latest_answer_at':board_stamp_or_none(latest_sub['at'])if latest_sub else None,'latest_answer_acknowledged_at':board_stamp_or_none(latest_sub['acknowledged_at'])if latest_sub and latest_sub['acknowledged_at']else None,'agent_seen_at':board_stamp_or_none(r_row['agent_seen_at']),'ack_seen_at':board_stamp_or_none(r_row['ack_seen_at']),'acknowledgements':acks_by_rep.get(rid,[]),'owner_requested':bool(r_row['owner_requested']),'sent_to_main':sent_flag,'updated_at':board_stamp_or_none(r_row['updated_at']),'published_at':board_stamp_or_none(r_row['published_at'])})
				out['worker_reports'][sid]=rep_list
				if is_self_allowed and out.get('role')=='main'and latest_by_rep:
					for(w_sid,w_list)in list((out.get('worker_reports')or{}).items()):
						if w_sid==sid or not isinstance(w_list,list):continue
						for r_item in w_list:
							if not isinstance(r_item,dict)or r_item.get('answered'):continue
							sub_hit=latest_by_rep.get(str(r_item.get('id')or''))
							if sub_hit:r_item['answered']=True;r_item['needs_answer']=False;r_item['latest_answer_id']=sub_hit['id'];r_item['latest_answer_at']=board_stamp_or_none(sub_hit['at'])
			except Exception:pass
		return out
	def board_state_sync(self,since=None,mode='full'):
		current=self.board_state();lock=getattr(self,'_board_sync_lock',None)
		if lock is None:lock=threading.RLock();self._board_sync_lock=lock;self._board_sync_rev=0;self._board_sync_sig='';self._board_sync_history={}
		with lock:
			sig=_preview_canonical_json(current)
			if self._board_sync_rev==0 or sig!=self._board_sync_sig:
				self._board_sync_rev+=1;self._board_sync_sig=sig;self._board_sync_history[self._board_sync_rev]=current;cutoff=self._board_sync_rev-16
				for rev_key in[k for k in self._board_sync_history if k<cutoff]:self._board_sync_history.pop(rev_key,None)
			cur_rev=self._board_sync_rev
			if since is not None and since==cur_rev:return{'ok':True,'unchanged':True,'revision':cur_rev,'mode':mode or'full'}
			if mode=='diff'and since is not None and since<cur_rev and since in self._board_sync_history:return{'ok':True,'unchanged':False,'revision':cur_rev,'mode':'diff','diff':compute_preview_board_diff(self._board_sync_history[since],current,since,cur_rev)}
			return{**current,'ok':True,'unchanged':False,'revision':cur_rev,'mode':'full'}
	def apply_board_report_op(self,op):
		if not isinstance(op,dict):return False
		op_name=str(op.get('op')or op.get('operation')or'answer').strip().lower();report_id=str(op.get('report_id')or op.get('id')or'').strip()
		if not report_id:raise ValueError('Missing report_id')
		if op_name in{'unpublish','remove','delete'}:
			try:self.unpublish(report_id,dismissed_by_owner=bool(op.get('from_owner',True)),_from_board=True)
			except FileNotFoundError:return False
			return True
		if op_name in{'answer','submit'}:
			answers=op.get('answers')
			if not isinstance(answers,dict):raise ValueError('Report answers must be an object')
			normalized_answers={}
			for(field_key,raw_ans)in answers.items():
				if isinstance(raw_ans,dict)and'choice'in raw_ans and'value'not in raw_ans:normalized_answers[field_key]=raw_ans['choice']
				else:normalized_answers[field_key]=raw_ans
			note_id=str(op.get('submission_id')or op.get('note_id')or uuid.uuid4().hex[:6])
			with self.transaction()as db:
				db.execute('BEGIN IMMEDIATE')
				try:report=self.report(report_id,shared=db)
				except FileNotFoundError:return False
				fields=parse_fields(report['markdown'])[1]
				if not fields:raise ValueError('This report has no fields to answer')
				self.submit(report_id,report['title'],fields,note_id,normalized_answers,shared=db,submission_format=report['submission_format'],revision=report['updated_at'])
			return True
		raise ValueError(f"Unsupported worker report operation: {op_name}")
	def apply_board_task_op(self,op):
		if not isinstance(op,dict):return
		op_name=str(op.get('op')or op.get('operation')or'set').strip().lower()
		if op_name=='clear':
			for item in list(self.list_tasks()):
				try:self.remove_task(item['id'])
				except Exception:pass
			return
		if op_name=='replace':
			for item in list(self.list_tasks()):
				try:self.remove_task(item['id'])
				except Exception:pass
			for item in op.get('tasks')if isinstance(op.get('tasks'),list)else[]:
				if not isinstance(item,dict)or not item.get('id'):continue
				details=item.get('details')
				if isinstance(details,str):details=[details]
				elif not isinstance(details,list):details=['Scope: .']
				self.write_task(str(item['id']),title=str(item.get('title')or item['id']),details=[str(x)for x in details],status=str(item.get('status')or'upcoming'),task_type=str(item.get('type')or item.get('task_type')or'code'),order=item.get('order'),report_id=item.get('report_id'))
			return
		if op_name in{'remove','delete'}:
			target_id=str(op.get('task_id')or op.get('id')or'').strip()
			if target_id:
				try:self.remove_task(target_id)
				except Exception:pass
			return
		if op_name=='reorder':
			order_ids=op.get('order_ids')
			if isinstance(order_ids,list)and order_ids:
				for(idx,tid)in enumerate(order_ids,start=1):
					if tid:
						try:self.write_task(str(tid),order=idx)
						except Exception:pass
				return
			target_id=str(op.get('task_id')or op.get('id')or'').strip()
			if target_id and op.get('order')is not None:self.write_task(target_id,order=int(op.get('order')))
			return
		target_id=str(op.get('task_id')or op.get('id')or'').strip()
		if not target_id:raise ValueError('Missing task_id')
		status='finished'if op_name=='finish'else op.get('status');details=op.get('details')
		if isinstance(details,str):details=[details]
		elif isinstance(details,list):details=[str(x)for x in details]
		else:details=None
		existing={row['id']:row for row in self.list_tasks()}.get(target_id)
		if existing is None and not details:scope_val=str(op.get('scope')or'.').strip();details=[f"Scope: {scope_val}"]
		elif details is not None and op.get('scope')and not any(str(line).startswith('Scope:')for line in details):details=[f"Scope: {str(op.get('scope')).strip()}",*details]
		self.write_task(target_id,title=op.get('title')or(existing['title']if existing else target_id),details=details,status=status,order=op.get('order'),append_details=bool(op.get('append')or op.get('append_details')),report_id=op.get('report_id'),task_type=op.get('task_type')or op.get('type')or(existing.get('type')if existing else'code'))
	def take_board_wake(self):
		raw_text=self.meta_value(BOARD_STATE_META)
		if not raw_text:return None
		try:raw=json.loads(raw_text)
		except Exception:return None
		if not isinstance(raw,dict):return None
		wakes=raw.get('wakes')
		if not isinstance(wakes,dict)or not wakes:return None
		sid=board_branch(workspace_branch(self.repository_root()));norm_check=normalize_board_state(raw,branch=sid);allowed=set(norm_check.get('allowed_sessions')or[]);wakes=dict(norm_check.get('wakes')or{})
		if not sid or sid not in allowed or not wakes.get(sid):return None
		stamp=wakes.pop(sid,None);raw['wakes']=wakes;ack_wakes=dict(raw.get('ack_wakes'))if isinstance(raw.get('ack_wakes'),dict)else{};hist_key=f"{sid}:history";seen=ack_wakes.get(hist_key);seen_list=[str(x)for x in seen if x]if isinstance(seen,list)else[];prev_single=ack_wakes.get(sid)
		if isinstance(prev_single,list):
			for item in prev_single:
				if item and str(item)not in seen_list:seen_list.append(str(item))
		elif prev_single and str(prev_single)not in seen_list:seen_list.append(str(prev_single))
		already_seen=bool(stamp and str(stamp)in seen_list)
		if stamp and not already_seen:seen_list.append(str(stamp))
		ack_wakes[sid]=str(stamp)if stamp else'';ack_wakes[hist_key]=seen_list[-64:];raw['ack_wakes']=ack_wakes
		if isinstance(raw.get('sessions'),list):
			for row in raw['sessions']:
				if isinstance(row,dict)and board_branch(row.get('branch')or row.get('session_id')or'')in{sid,branch_slug_tail(sid)}:row['polling']=False;row['status']='active'
		with self.transaction(autosave=False)as db:db.execute('INSERT OR REPLACE INTO meta VALUES (?, ?)',(BOARD_STATE_META,json.dumps(raw,ensure_ascii=False)))
		return None if already_seen else str(stamp)
	def take_unseen_board_entries(self):
		board_now=self.board_state();sid=board_branch(workspace_branch(self.repository_root()));entries=board_now.get('entries')if isinstance(board_now.get('entries'),list)else[]
		if not sid or not entries:return[]
		is_worker=board_now.get('board_role')=='worker'or bool(board_now.get('main_session_id'))and board_now.get('main_session_id')!=sid;raw_text=self.meta_value(BOARD_STATE_META)
		try:raw=json.loads(raw_text)if raw_text else{}
		except Exception:raw={}
		if not isinstance(raw,dict):raw={}
		ack_wakes=dict(raw.get('ack_wakes'))if isinstance(raw.get('ack_wakes'),dict)else{};entries_key=f"{sid}:entries";seen_raw=ack_wakes.get(entries_key);seen_list=[str(x)for x in seen_raw if x]if isinstance(seen_raw,list)else[];seen_set=set(seen_list);unseen=[]
		for entry in entries:
			if not isinstance(entry,dict):continue
			entry_id=str(entry.get('id')or'')
			if entry_id and entry_id not in seen_set:
				seen_set.add(entry_id);seen_list.append(entry_id)
				if entry.get('branch')==sid:continue
				if is_worker:
					if entry.get('to')==sid or entry.get('mention_target')==sid:unseen.append(entry)
				elif entry.get('to')in('*',sid,'',None)or entry.get('mention_target')==sid:unseen.append(entry)
		ack_wakes[entries_key]=seen_list[-512:];raw['ack_wakes']=ack_wakes
		with self.transaction(autosave=False)as db:db.execute('INSERT OR REPLACE INTO meta VALUES (?, ?)',(BOARD_STATE_META,json.dumps(raw,ensure_ascii=False)))
		return unseen
	def take_board_notifications(self,*,include_peer_notices=True):
		raw_text=self.meta_value(BOARD_STATE_META)
		if not raw_text:return[]
		try:raw=json.loads(raw_text)
		except Exception:return[]
		if not isinstance(raw,dict):return[]
		board_now=self.board_state();sid=str(board_now.get('branch')or'');allowed=set(board_now.get('allowed_sessions')or[])
		if not sid or sid not in allowed:return[]
		ack_wakes=dict(raw.get('ack_wakes'))if isinstance(raw.get('ack_wakes'),dict)else{};multi_repo=bool(board_now.get('multi_repo'));current_repo=clean_board_repo(board_now.get('repo')or'',sid);active_repos={current_repo,*(board_now.get('linked_repos')or[])};active_repos.discard('');notices=[];changed=False
		if include_peer_notices:
			joined_key=f"{sid}:joined_sessions";raw_joined=ack_wakes.get(joined_key);seen_joined={str(k):str(v)for(k,v)in raw_joined.items()if k}if isinstance(raw_joined,dict)else{};disc_key=f"{sid}:disconnected_sessions";raw_disc=ack_wakes.get(disc_key);seen_disc={str(x)for x in raw_disc if x}if isinstance(raw_disc,list)else set();active_other_sids=set()
			for row in board_now.get('sessions')or[]:
				if not isinstance(row,dict)or not row.get('allowed'):continue
				other_sid=str(row.get('branch')or'')
				if not other_sid or other_sid==sid:continue
				other_repo=clean_board_repo(row.get('repo')or'',other_sid)
				if active_repos and other_repo and other_repo not in active_repos:continue
				active_other_sids.add(other_sid);other_board_id=str(row.get('board_id')or other_sid);other_role=str(row.get('role')or'worker');short_id='alpha'if other_role=='main'else str(row.get('worker_id')or other_board_id);ident_label=other_board_id if multi_repo or short_id==other_board_id else f"{short_id} ({other_board_id})";prev_board_id=seen_joined.get(other_sid)
				if prev_board_id!=other_board_id:
					if prev_board_id is None:notices.append(f"BOARD SESSION JOINED: session {other_sid} joined the board with identifier {ident_label} ({other_role}).")
					else:notices.append(f"BOARD SESSION UPDATED: session {other_sid} now uses identifier {ident_label} ({other_role}).")
					seen_joined[other_sid]=other_board_id;changed=True
				is_disc=bool(row.get('disconnected')or row.get('status')=='disconnected')
				if is_disc and other_sid not in seen_disc:notices.append(f"BOARD SESSION DISCONNECTED: session {other_sid} ({ident_label}, {other_role}) disconnected from the board.");seen_disc.add(other_sid);changed=True
				elif not is_disc and other_sid in seen_disc:notices.append(f"BOARD SESSION RECONNECTED: session {other_sid} ({ident_label}, {other_role}) reconnected to the board.");seen_disc.discard(other_sid);changed=True
			for stale_sid in[item for item in seen_joined if item not in active_other_sids]:seen_joined.pop(stale_sid,None);seen_disc.discard(stale_sid);changed=True
			ack_wakes[joined_key]=seen_joined;ack_wakes[disc_key]=sorted(seen_disc)
		worker_key=f"{sid}:worker_role_notified"
		if board_now.get('board_role')=='worker':
			self_board_id=str(board_now.get('board_id')or sid);self_short_id=str(board_now.get('worker_id')or self_board_id);self_ident=self_board_id if multi_repo or self_short_id==self_board_id else f"{self_short_id} ({self_board_id})"
			if ack_wakes.get(worker_key)!=self_board_id:
				root=self.repository_root();worker_md='.agents/skills/arena-skill/WORKER.md'
				for candidate in('.agents/skills/arena-skill/WORKER.md','skills/arena-skill/WORKER.md'):
					if(root/candidate).is_file():worker_md=candidate;break
				notices.append(f"WORKER ROLE ASSIGNED: you are assigned the worker role with identifier {self_ident}. Read {worker_md} before continuing work.");ack_wakes[worker_key]=self_board_id;changed=True
			if include_peer_notices:
				heads_key=f"{sid}:main_branch_heads";raw_heads=ack_wakes.get(heads_key);seen_heads={str(k):dict(v)for(k,v)in raw_heads.items()if k and isinstance(v,dict)}if isinstance(raw_heads,dict)else{};active_main_sids=set()
				for row in board_now.get('sessions')or[]:
					if not isinstance(row,dict)or not row.get('allowed'):continue
					if str(row.get('role')or'')!='main':continue
					m_sid=str(row.get('branch')or'')
					if not m_sid or m_sid==sid:continue
					m_repo=clean_board_repo(row.get('repo')or'',m_sid)
					if active_repos and m_repo and m_repo not in active_repos:continue
					active_main_sids.add(m_sid);m_ident=str(row.get('board_id')or m_sid);m_branch=m_sid;prev_rec=dict(seen_heads.get(m_sid)or{});cur_rh=str(row.get('remote_head')or'').strip().lower();cur_om=str(row.get('origin_main_head')or'').strip().lower();prev_rh=str(prev_rec.get('remote_head')or'').strip().lower();prev_om=str(prev_rec.get('origin_main_head')or'').strip().lower()
					if GIT_SHA_RE.fullmatch(cur_rh):
						if GIT_SHA_RE.fullmatch(prev_rh)and prev_rh!=cur_rh:notices.append(f"MAIN BRANCH MOVED: main session {m_ident} pushed {m_branch} ({prev_rh[:8]} -> {cur_rh[:8]}).");changed=True
						if prev_rh!=cur_rh:prev_rec['remote_head']=cur_rh;changed=True
					if GIT_SHA_RE.fullmatch(cur_om):
						if GIT_SHA_RE.fullmatch(prev_om)and prev_om!=cur_om:notices.append(f"MAIN BRANCH MOVED: origin/main advanced ({prev_om[:8]} -> {cur_om[:8]}) on {m_ident}. Fetch and rebase if needed.");changed=True
						if prev_om!=cur_om:prev_rec['origin_main_head']=cur_om;changed=True
					if prev_rec:seen_heads[m_sid]=prev_rec
				for stale_m in[k for k in seen_heads if k not in active_main_sids]:seen_heads.pop(stale_m,None);changed=True
				ack_wakes[heads_key]=seen_heads
		elif worker_key in ack_wakes:ack_wakes.pop(worker_key,None);ack_wakes.pop(f"{sid}:main_branch_heads",None);changed=True
		if changed:
			raw['ack_wakes']=ack_wakes
			if notices and isinstance(raw.get('sessions'),list):
				for row in raw['sessions']:
					if isinstance(row,dict)and board_branch(row.get('branch')or row.get('session_id')or'')in{sid,branch_slug_tail(sid)}:row['polling']=False;row['status']='active'
			with self.transaction(autosave=False)as db:db.execute('INSERT OR REPLACE INTO meta VALUES (?, ?)',(BOARD_STATE_META,json.dumps(raw,ensure_ascii=False)))
		return notices
	def board_action(self,payload):
		if not isinstance(payload,dict):raise TypeError('Board payload must be an object')
		action_type=str(payload.get('action')or payload.get('type')or'').strip().lower();raw_text=self.meta_value(BOARD_STATE_META)
		try:raw_stored=json.loads(raw_text)if raw_text else{}
		except Exception:raw_stored={}
		ack_wakes=dict(raw_stored.get('ack_wakes'))if isinstance(raw_stored,dict)and isinstance(raw_stored.get('ack_wakes'),dict)else{};state=self.board_state(_scope_repos=False);sid=state['branch'];linked_repos=[str(x)for x in state.get('linked_repos')or[]if x];known_repos=[str(x)for x in state.get('known_repos')or[]if x];egress_version=str(state.get('egress_version')or'').strip();multi_repo=bool(state.get('multi_repo')or linked_repos);worker_ids=dict(state.get('worker_ids')or{});worker_tasks={k:[dict(item)for item in v if isinstance(item,dict)]for(k,v)in(state.get('worker_tasks')or{}).items()if isinstance(v,list)};task_ops=[dict(row)for row in state.get('task_ops')or[]];worker_reports={k:[dict(item)for item in v if isinstance(item,dict)]for(k,v)in(state.get('worker_reports')or{}).items()if isinstance(v,list)};report_ops=[dict(row)for row in state.get('report_ops')or[]];removed_sessions=[str(x)for x in state.get('removed_sessions')or[]if x and x!=sid];allowed=list(state['allowed_sessions']);main_sid=state['main_session_id'];sessions=[dict(row)for row in state['sessions']];entries=[dict(row)for row in state['entries']];wakes=dict(state['wakes'])
		if action_type=='sync':
			if'board_diff'in payload and payload.get('board_diff')is not None:
				diff_obj=payload.get('board_diff')
				if not isinstance(diff_obj,dict):raise TypeError('board_diff must be an object')
				base_rev=diff_obj.get('base_rev');next_rev=diff_obj.get('rev')
				if type(base_rev)is not int or type(next_rev)is not int:raise TypeError('board_diff base_rev and rev must be integers')
				push_cursor=getattr(self,'_board_push_cursor',None)
				if push_cursor is None or push_cursor[0]!=base_rev:return{'ok':False,'error':'stale-revision','need_full':True}
				incoming=apply_preview_board_diff(push_cursor[1],diff_obj);self._board_push_cursor=next_rev,incoming
			else:
				incoming=dict(payload.get('board'))if isinstance(payload.get('board'),dict)else dict(payload)
				if payload.get('client_rev')is not None and isinstance(payload.get('board'),dict):self._board_push_cursor=int(payload['client_rev']),dict(payload['board'])
			if isinstance(incoming,dict)and isinstance(incoming.get('linked_repos'),list):
				linked_repos=[]
				for item in incoming['linked_repos']:
					slug=clean_board_repo(item)
					if slug and slug not in linked_repos:linked_repos.append(slug)
			if isinstance(incoming,dict)and str(incoming.get('egress_version')or'').strip():egress_version=str(incoming.get('egress_version')or'').strip()
			elif str(payload.get('egress_version')or'').strip():egress_version=str(payload.get('egress_version')or'').strip()
			if egress_version:self.set_egress_version(egress_version)
			if isinstance(incoming,dict)and'multi_repo'in incoming:multi_repo=bool(incoming.get('multi_repo')or linked_repos)
			merged=normalize_board_state(incoming,repo=state['repo'],branch=sid,polling=any(r['branch']==sid and r.get('polling')for r in sessions),scope_repos=False)
			if isinstance(incoming,dict)and isinstance(incoming.get('known_repos'),list):known_repos=list(merged.get('known_repos')or[])
			incoming_has_allow=isinstance(incoming,dict)and('allowed_sessions'in incoming or'removed_sessions'in incoming);merged_allowed_set=set(merged.get('allowed_sessions')or[]);protected_allowed_set=merged_allowed_set if incoming_has_allow else set(allowed)|merged_allowed_set;self_revoked=False
			for rem_sid in merged.get('removed_sessions')or[]:
				if not rem_sid or rem_sid in protected_allowed_set:continue
				if rem_sid==sid:self_revoked=True;continue
				if rem_sid not in removed_sessions:removed_sessions.append(rem_sid)
			if protected_allowed_set:removed_sessions=[r_sid for r_sid in removed_sessions if r_sid not in protected_allowed_set]
			removed_set=set(removed_sessions)
			if self_revoked:
				allowed=[item for item in allowed if item!=sid]
				if main_sid==sid:main_sid=''
			allowed=[item for item in allowed if item not in removed_set];sessions=[row for row in sessions if row['branch']not in removed_set]
			for(k,v)in(merged.get('worker_ids')or{}).items():
				if k not in removed_set:worker_ids[k]=v
			for(k,v)in(merged.get('worker_tasks')or{}).items():
				if k!=sid and k not in removed_set:worker_tasks[k]=[dict(item)for item in v if isinstance(item,dict)]
			for(k,v)in(merged.get('worker_reports')or{}).items():
				if k!=sid and k not in removed_set:worker_reports[k]=[dict(item)for item in v if isinstance(item,dict)]
			seen_op_ids={str(op.get('id')or'')for op in task_ops if op.get('id')};applied_key=f"{sid}:task_ops";applied_list=[str(x)for x in ack_wakes.get(applied_key)if x]if isinstance(ack_wakes.get(applied_key),list)else[];applied_set=set(applied_list);cleared_at=str(self.meta_value(CLEARED_AT_META)or'')
			for op in merged.get('task_ops')or[]:
				op_id=str(op.get('id')or'')
				if op_id and op_id not in seen_op_ids:seen_op_ids.add(op_id);task_ops.append(dict(op))
				target_sid=resolve_board_target(op.get('target')or'',sessions,allowed=allowed,main_sid=main_sid)
				if op_id and target_sid==sid and op_id not in applied_set:
					applied_set.add(op_id);applied_list.append(op_id);cleared_ms=board_stamp_ms(cleared_at);op_ms=board_stamp_ms(op.get('at'))
					if not(cleared_ms is not None and op_ms is not None and op_ms<=cleared_ms):self.apply_board_task_op(op);wakes[sid]=op_id
			ack_wakes[applied_key]=applied_list[-128:];seen_rep_op_ids={str(op.get('id')or'')for op in report_ops if op.get('id')};applied_rep_key=f"{sid}:report_ops";applied_rep_list=[str(x)for x in ack_wakes.get(applied_rep_key)if x]if isinstance(ack_wakes.get(applied_rep_key),list)else[];applied_rep_set=set(applied_rep_list)
			for op in merged.get('report_ops')or[]:
				op_id=str(op.get('id')or'')
				if op_id and op_id not in seen_rep_op_ids:seen_rep_op_ids.add(op_id);report_ops.append(dict(op))
				if str(op.get('op')or'')=='entry-ack':continue
				target_sid=resolve_board_target(op.get('target')or'',sessions,allowed=allowed,main_sid=main_sid)
				if target_sid and target_sid in worker_reports:worker_reports[target_sid]=apply_report_op_to_list(worker_reports.get(target_sid)or[],op)
				if op_id and target_sid==sid and op_id not in applied_rep_set:
					applied_rep_set.add(op_id);applied_rep_list.append(op_id);cleared_ms=board_stamp_ms(cleared_at);op_ms=board_stamp_ms(op.get('at'))
					if not(cleared_ms is not None and op_ms is not None and op_ms<=cleared_ms)and self.apply_board_report_op(op):wakes[sid]=op_id
			ack_wakes[applied_rep_key]=applied_rep_list[-128:]
			if merged['allowed_sessions']or not allowed:
				for item in merged['allowed_sessions']:
					if item not in removed_set and item not in allowed:allowed.append(item)
			allowed_sync_set=set(allowed);worker_tasks={k:v for(k,v)in worker_tasks.items()if k in allowed_sync_set};worker_reports={k:v for(k,v)in worker_reports.items()if k in allowed_sync_set or sid and k==sid}
			if isinstance(incoming,dict)and'worker_ids'in incoming and merged['main_session_id']and merged['main_session_id']not in(merged.get('worker_ids')or{}):worker_ids.pop(merged['main_session_id'],None)
			if merged['main_session_id']and merged['main_session_id']not in removed_set and(merged['main_session_id']not in worker_ids or not main_sid or main_sid in worker_ids):main_sid=merged['main_session_id']
			elif main_sid in removed_set or main_sid and main_sid not in allowed:main_sid=''
			by_sid={row['branch']:dict(row)for row in sessions}
			for row in merged['sessions']:
				row_sid=row['branch']
				if row_sid in removed_set:continue
				if row_sid==sid and row_sid in by_sid:continue
				prev_existing=by_sid.get(row_sid,{});prev_survey=prev_existing.get('survey');by_sid[row_sid]=dict(row)
				if not isinstance(by_sid[row_sid].get('survey'),dict)and isinstance(prev_survey,dict):by_sid[row_sid]['survey']=prev_survey
				for status_key in('polling_since','waiting_for_gates','checking_pr','calls_since_message','gate_threshold','agent_seen_at','turn_ended_at','agent_call_ended_at','remote_head','origin_main_head'):
					if status_key not in by_sid[row_sid]and status_key in prev_existing and prev_existing[status_key]is not None:
						if status_key=='polling_since'and not by_sid[row_sid].get('polling'):continue
						if status_key=='turn_ended_at':
							if by_sid[row_sid].get('polling'):continue
							prev_turn_ms=board_stamp_ms(prev_existing[status_key]);active_ms=[ms for ms in(board_stamp_ms(by_sid[row_sid].get('agent_seen_at')),board_stamp_ms(by_sid[row_sid].get('agent_call_ended_at')))if ms is not None]
							if active_ms and(prev_turn_ms is None or max(active_ms)>prev_turn_ms):continue
						by_sid[row_sid][status_key]=prev_existing[status_key]
			sessions=list(by_sid.values());existing_entry_ids={str(item.get('id')or'')for item in entries if item.get('id')};seen_wakes=ack_wakes.get(f"{sid}:history");seen_wake_set={str(x)for x in seen_wakes if x}if isinstance(seen_wakes,list)else set();seen_entries=ack_wakes.get(f"{sid}:entries")
			if isinstance(seen_entries,list):seen_wake_set.update(str(x)for x in seen_entries if x)
			prev_ack=ack_wakes.get(sid)
			if isinstance(prev_ack,list):seen_wake_set.update(str(x)for x in prev_ack if x)
			elif prev_ack:seen_wake_set.add(str(prev_ack))
			entry_by_id={};merged_entries=[]
			for item in[*entries,*merged['entries']]:
				row=dict(item);eid=str(row.get('id')or'')
				if eid and eid in entry_by_id:
					prev_entry=entry_by_id[eid]
					for field in('seen_at','acknowledged_at','ack_type','ack_text','ack_sender','ack_edited_at','task_id'):
						if row.get(field)and not prev_entry.get(field):prev_entry[field]=row[field]
					if int(row.get('ack_edited_seen_count')or 0)>int(prev_entry.get('ack_edited_seen_count')or 0):prev_entry['ack_edited_seen_count']=int(row.get('ack_edited_seen_count')or 0)
					if isinstance(row.get('replies'),list)and len(row['replies'])>len(prev_entry.get('replies')or[]):
						prev_entry['replies']=row['replies']
						if row.get('ack_edited_at'):prev_entry['ack_edited_at']=row['ack_edited_at']
					continue
				if eid:entry_by_id[eid]=row
				if isinstance(row.get('file'),dict)and'content'in row['file']:row['file']=stage_board_file(self.path.parent,row['branch'],row['file'])
				merged_entries.append(row)
			for op in report_ops:
				if str(op.get('op')or'')!='entry-ack':continue
				target_eid=str(op.get('entry_id')or'')
				if not target_eid or target_eid not in entry_by_id:continue
				target_entry=entry_by_id[target_eid]
				for field in('seen_at','acknowledged_at','ack_type','ack_text','ack_sender','ack_edited_at','task_id'):
					if op.get(field)and not target_entry.get(field):target_entry[field]=resolve_board_ack_sender(op[field],sessions,allowed=allowed,main_sid=main_sid,from_sid=str(target_entry.get('branch')or''),to_sid=str(target_entry.get('to')or''))if field=='ack_sender'else op[field]
				if int(op.get('ack_edited_seen_count')or 0)>int(target_entry.get('ack_edited_seen_count')or 0):target_entry['ack_edited_seen_count']=int(op.get('ack_edited_seen_count')or 0)
				if isinstance(op.get('replies'),list)and len(op['replies'])>len(target_entry.get('replies')or[]):
					target_entry['replies']=op['replies']
					if op.get('ack_edited_at'):target_entry['ack_edited_at']=op['ack_edited_at']
			is_worker_sid=bool(main_sid and sid and sid!=main_sid)
			for row in merged_entries:
				eid=str(row.get('id')or'');direct_hit=row.get('to')==sid or row.get('mention_target')==sid;target_hit=direct_hit if is_worker_sid else direct_hit or row.get('to')in('*','',None)
				if eid and eid not in existing_entry_ids and row.get('branch')!=sid and target_hit and sid in allowed and eid not in seen_wake_set:wakes[sid]=eid
			entries=sorted(merged_entries,key=lambda r:(board_stamp_ms(r.get('at'))if board_stamp_ms(r.get('at'))is not None else-1,str(r.get('id')or'')))
			with closing(self.connect())as db:
				for b_entry in entries:
					note_ref=str(b_entry.get('note_id')or b_entry.get('id')or'')
					if not note_ref:continue
					n_row=db.execute('SELECT seen_at, acknowledged_at, ack_type, ack_text, ack_edited_at, replies, task_id FROM notes WHERE id = ?',(note_ref,)).fetchone()
					if n_row is None:continue
					for n_field in('seen_at','acknowledged_at','ack_type','ack_text','ack_edited_at','task_id'):
						if n_row[n_field]and not b_entry.get(n_field):b_entry[n_field]=board_stamp(n_row[n_field])if n_field.endswith('_at')else n_row[n_field]
					n_replies=replies_list(n_row['replies'])
					if len(n_replies)>len(b_entry.get('replies')or[]):b_entry['replies']=[{'type':r.get('type')or'reply','text':r.get('text')or'','at':board_stamp(r.get('at'))}for r in n_replies if isinstance(r,dict)]
			sent_wakes_key=f"{sid}:sent_wakes";sent_wakes=dict(ack_wakes.get(sent_wakes_key))if isinstance(ack_wakes.get(sent_wakes_key),dict)else{}
			for(k,v)in merged['wakes'].items():
				if k==sid and str(v)in seen_wake_set:continue
				if not wakes.get(k)or str(v)>=str(wakes[k]):wakes[k]=v
				if k!=sid and v:sent_wakes[k]=str(v)
			if'wakes'in incoming:
				for peer_sid in list(wakes):
					if peer_sid==sid or peer_sid in merged['wakes']:continue
					cur_wake=str(wakes.get(peer_sid)or'');peer_row=by_sid.get(peer_sid,{});peer_poll_since=str(peer_row.get('polling_since')or'');wake_iso_match=re.match('^(\\d{4}-\\d{2}-\\d{2}T\\d{2}:\\d{2}:\\d{2})',cur_wake);wake_iso=wake_iso_match.group(1)if wake_iso_match else''
					if sent_wakes.get(peer_sid)==cur_wake or peer_row.get('polling')and(not wake_iso or peer_poll_since and peer_poll_since>=wake_iso):wakes.pop(peer_sid,None);sent_wakes.pop(peer_sid,None)
			ack_wakes[sent_wakes_key]=sent_wakes
			for row in sessions:
				if row['branch']in allowed and wakes.get(row['branch']):row['polling']=False;row['status']='active'
		elif action_type in{'worker-task','manage-worker-tasks'}:
			sender=resolve_board_target(payload.get('branch')or sid,sessions,allowed=allowed,main_sid=main_sid)
			if main_sid and sender and sender!=main_sid:raise ValueError('Only the main session can manage worker tasks')
			target=resolve_board_target(payload.get('target')or payload.get('worker')or payload.get('target_branch')or'',sessions,allowed=allowed,main_sid=main_sid)
			if not target:raise ValueError('Missing worker target')
			op_name=str(payload.get('op')or payload.get('operation')or payload.get('subaction')or'set').strip().lower()
			if op_name not in{'set','create','update','finish','reorder','remove','delete','clear','replace'}:raise ValueError(f"Unsupported worker task operation: {op_name}")
			op_record={'id':str(payload.get('id')or f"top-{uuid.uuid4().hex[:8]}"),'target':target,'op':op_name,'task_id':str(payload.get('task_id')or payload.get('task')or''),'title':payload.get('title'),'details':payload.get('details'),'scope':payload.get('scope'),'status':payload.get('status'),'task_type':payload.get('task_type')or payload.get('type'),'order':payload.get('order'),'order_ids':payload.get('order_ids'),'append':bool(payload.get('append')),'report_id':payload.get('report_id'),'tasks':payload.get('tasks'),'at':board_stamp()};task_ops.append(op_record);worker_tasks[target]=apply_task_op_to_list(worker_tasks.get(target)or[],op_record)
			if target==sid:
				applied_key=f"{sid}:task_ops";applied_list=[str(x)for x in ack_wakes.get(applied_key)if x]if isinstance(ack_wakes.get(applied_key),list)else[]
				if op_record['id']not in applied_list:applied_list.append(op_record['id']);ack_wakes[applied_key]=applied_list[-128:];self.apply_board_task_op(op_record)
			if target in allowed:
				wakes[target]=op_record['id'];ack_wakes.pop(target,None)
				for row in sessions:
					if row['branch']==target:row['polling']=False;row['status']='active'
		elif action_type in{'worker-report','manage-worker-reports'}:
			sender=resolve_board_target(payload.get('branch')or sid,sessions,allowed=allowed,main_sid=main_sid);from_owner=bool(payload.get('from_owner'))
			if not from_owner and main_sid and sender and sender!=main_sid:raise ValueError('Only the main session can manage worker reports')
			target=resolve_board_target(payload.get('target')or payload.get('worker')or payload.get('target_branch')or'',sessions,allowed=allowed,main_sid=main_sid)
			if not target:raise ValueError('Missing worker target')
			op_name=str(payload.get('op')or payload.get('operation')or payload.get('subaction')or'answer').strip().lower()
			if op_name not in{'answer','submit','unpublish','remove','delete'}:raise ValueError(f"Unsupported worker report operation: {op_name}")
			report_id=str(payload.get('report_id')or payload.get('report')or'').strip()
			if not report_id:raise ValueError('Missing report_id')
			if op_name in{'answer','submit'}and not payload.get('from_owner'):
				target_rep=next((r for r in worker_reports.get(target)or[]if isinstance(r,dict)and r.get('id')==report_id),None);sent_via_entry=any(e.get('branch')==target and(report_id in str(e.get('text')or'')or str((e.get('payload')or{}).get('report_id')or'')==report_id)for e in entries if isinstance(e,dict));is_sent_to_main=bool(sent_via_entry or target_rep and target_rep.get('sent_to_main'));is_owner_requested=bool(target_rep and target_rep.get('owner_requested')or payload.get('owner_requested'));has_explicit_report_entries=any(isinstance(e,dict)and e.get('branch')==target and str((e.get('payload')or{}).get('report_id')or'')for e in entries)
				if is_owner_requested or has_explicit_report_entries and not is_sent_to_main:raise ValueError(f"Report {report_id!r} was not sent to main; owner-requested reports must be presented to the owner")
			rep_op={'id':str(payload.get('id')or f"rop-{uuid.uuid4().hex[:8]}"),'target':target,'op':op_name,'report_id':report_id,'from_owner':from_owner,'answers':payload.get('answers')if isinstance(payload.get('answers'),dict)else{},'submission_id':payload.get('submission_id')or payload.get('note_id'),'at':board_stamp()};report_ops.append(rep_op);worker_reports[target]=apply_report_op_to_list(worker_reports.get(target)or[],rep_op)
			if target==sid:
				applied_rep_key=f"{sid}:report_ops";applied_rep_list=[str(x)for x in ack_wakes.get(applied_rep_key)if x]if isinstance(ack_wakes.get(applied_rep_key),list)else[]
				if rep_op['id']not in applied_rep_list:applied_rep_list.append(rep_op['id']);ack_wakes[applied_rep_key]=applied_rep_list[-128:];self.apply_board_report_op(rep_op)
			if target in allowed:
				wakes[target]=rep_op['id'];ack_wakes.pop(target,None)
				for row in sessions:
					if row['branch']==target:row['polling']=False;row['status']='active'
		elif action_type=='allow':
			target=resolve_board_target(payload.get('branch')or sid,sessions,allowed=allowed,main_sid=main_sid)
			if not target:raise ValueError('Missing branch')
			removed_sessions=[item for item in removed_sessions if item!=target];raw_wid=payload.get('worker_id')or payload.get('identifier')
			if raw_wid is not None:worker_ids[target]=clean_worker_id(raw_wid,required=True)
			if target not in allowed:allowed.append(target)
			found_target=False
			for row in sessions:
				if row['branch']==target:row['updated_at']=board_stamp();found_target=True
			if not found_target:sessions.append({'branch':target,'repo':state['repo'],'status':'active','polling':False,'updated_at':board_stamp()})
		elif action_type in{'delegate-id','set-worker-id'}:
			target=resolve_board_target(payload.get('branch')or payload.get('target')or'',sessions,allowed=allowed,main_sid=main_sid)
			if not target:raise ValueError('Missing branch')
			raw_wid=payload.get('worker_id')if payload.get('worker_id')is not None else payload.get('identifier')if payload.get('identifier')is not None else payload.get('id');worker_ids[target]=clean_worker_id(raw_wid,required=True)
		elif action_type=='set-multi-repo':
			multi_repo=bool(payload.get('multi_repo')or payload.get('enabled'))
			if not multi_repo:linked_repos=[]
		elif action_type=='link-repo':
			slug=clean_board_repo(payload.get('repo')or payload.get('target')or'')
			if not slug:raise ValueError('Missing repo')
			if slug not in linked_repos:linked_repos.append(slug)
			multi_repo=bool(linked_repos)
		elif action_type=='unlink-repo':slug=clean_board_repo(payload.get('repo')or payload.get('target')or'');linked_repos=[item for item in linked_repos if item!=slug];multi_repo=bool(linked_repos)
		elif action_type in{'revoke','remove-session','dismiss'}:
			target=resolve_board_target(payload.get('branch')or payload.get('target')or'',sessions,allowed=allowed,main_sid=main_sid)
			if target:
				if target!=sid and target not in removed_sessions:removed_sessions.append(target)
				allowed=[item for item in allowed if item!=target];sessions=[row for row in sessions if row['branch']!=target];wakes.pop(target,None);worker_ids.pop(target,None);worker_tasks.pop(target,None);worker_reports.pop(target,None)
				if main_sid==target:main_sid=allowed[0]if allowed else''
		elif action_type=='set-main':
			target=resolve_board_target(payload.get('branch')or'',sessions,allowed=allowed,main_sid=main_sid)
			if target:
				removed_sessions=[item for item in removed_sessions if item!=target]
				if target not in allowed:allowed.append(target)
				worker_ids.pop(target,None)
				for row in sessions:
					if row['branch']==target:row['worker_id']='';row['role']='main';row['updated_at']=board_stamp()
			main_sid=target
		elif action_type=='heartbeat':
			target=board_branch(payload.get('branch')or sid)
			if target:
				removed_sessions=[item for item in removed_sessions if item!=target];is_poll=bool(payload.get('polling')or payload.get('status')=='polling');updated=False
				for row in sessions:
					if row['branch']==target:row['repo']=str(payload.get('repo')or row.get('repo')or state['repo']);row['status']='polling'if is_poll else'active';row['polling']=is_poll;row['updated_at']=board_stamp();updated=True
				if not updated:sessions.append({'branch':target,'repo':str(payload.get('repo')or state['repo']),'status':'polling'if is_poll else'active','polling':is_poll,'updated_at':board_stamp()})
		elif action_type=='post':
			sender=resolve_board_target(payload.get('branch')or sid,sessions,allowed=allowed,main_sid=main_sid)
			if not sender or sender not in allowed:raise ValueError('Session is not on the board allowlist')
			body=payload.get('payload')
			if not isinstance(body,dict):body={'text':str(body)}if body is not None else{}
			if not body:body={k:v for(k,v)in payload.items()if k not in{'action','type','id','board_id','from_owner','note_id','mention_target','to','repo','branch','at','seen_at','acknowledged_at','ack_type','ack_text','ack_edited_at','replies','payload','file'}}
			entry_text=str(payload.get('text')or payload.get('body')or payload.get('message')or body.get('text')or body.get('body')or body.get('message')or body.get('summary')or'');mention_target='';raw_mention=str(payload.get('mention_target')or'').strip()
			if raw_mention:mention_target=resolve_board_target(raw_mention,sessions,allowed=allowed,main_sid=main_sid)
			if not mention_target and entry_text:
				for token in re.findall('(?:^|(?<=\\s))@([A-Za-z0-9_/-]{1,64})\\b',entry_text):
					cand=resolve_board_target(token,sessions,allowed=allowed,main_sid=main_sid)
					if cand and cand in allowed and cand!=sender:mention_target=cand;break
			has_explicit_to='to'in payload and payload.get('to')is not None;raw_to=str(payload.get('to')or'').strip()if has_explicit_to else''
			if has_explicit_to:resolved_to=resolve_board_target(raw_to,sessions,allowed=allowed,main_sid=main_sid)if raw_to and raw_to not in{'*','all','@all'}else''
			else:resolved_to=mention_target if mention_target in allowed else''
			if not resolved_to or resolved_to not in allowed:valid_str=', '.join(valid_board_targets(sessions,allowed=allowed,main_sid=main_sid));raise ValueError(f"Unrecognized board target {raw_to!r}; valid targets: {valid_str}")
			staged_file=None
			if payload.get('file')is not None:staged_file=stage_board_file(self.path.parent,sender,payload.get('file'))
			if entry_text and'text'not in body and not body.get('tag'):body['text']=entry_text
			if body.get('text'):body.pop('body',None);body.pop('message',None)
			tag_value=str(payload.get('tag')or body.get('tag')or'').strip()
			if not entry_text.strip()and not tag_value and staged_file is None:raise ValueError('Board post requires non-empty text, a tag, or a file attachment')
			entry_text=normalize_board_mentions(entry_text,sessions,allowed=allowed,main_sid=main_sid,from_sid=sender,to_sid=resolved_to)
			if entry_text and('text'in body or not body.get('tag')):body['text']=entry_text
			entry_id=str(payload.get('id')or f"p{uuid.uuid4().hex[:5]}");sender_row=next((r for r in sessions if r['branch']==sender),{});from_owner=bool(payload.get('from_owner'));entries.append({'id':entry_id,'branch':sender,'board_id':str(payload.get('board_id')or sender_row.get('board_id')or sender),'from_owner':from_owner,'note_id':str(payload.get('note_id')or''),'to':resolved_to,'mention_target':mention_target,'repo':str(payload.get('repo')or sender_row.get('repo')or state['repo']),'at':board_stamp(),'tag':str(payload.get('tag')or body.get('tag')or''),'text':entry_text,'seen_at':None,'acknowledged_at':None,'ack_type':None,'ack_text':None,'ack_edited_at':None,'replies':[],'payload':body,'file':staged_file});direct_target=mention_target or resolved_to
			if direct_target and direct_target in allowed and direct_target!=sender:
				for row in sessions:
					if row['branch']==direct_target:row['polling']=False;row['status']='active'
				wakes[direct_target]=entry_id;ack_wakes.pop(direct_target,None)
		elif action_type=='wake':
			target=str(payload.get('target')or payload.get('branch')or'').strip();stamp=f"{board_stamp()}-{uuid.uuid4().hex[:6]}"
			if target=='all-polling':
				for row in sessions:
					if row['branch']in allowed and row.get('polling'):row['polling']=False;row['status']='active';wakes[row['branch']]=stamp;ack_wakes.pop(row['branch'],None)
			else:
				norm_target=resolve_board_target(target,sessions,allowed=allowed,main_sid=main_sid)
				if norm_target:
					for row in sessions:
						if row['branch']==norm_target and norm_target in allowed:row['polling']=False;row['status']='active'
					if norm_target in allowed:wakes[norm_target]=stamp;ack_wakes.pop(norm_target,None)
		elif action_type=='clear':entries=[]
		else:raise ValueError(f"Unknown board action: {action_type}")
		report_ops=trim_report_ops(report_ops);stored={'version':1,'egress_version':egress_version,'local_branch':sid,'multi_repo':multi_repo,'linked_repos':linked_repos,'known_repos':known_repos,'removed_sessions':removed_sessions,'worker_ids':worker_ids,'worker_tasks':worker_tasks,'task_ops':task_ops,'worker_reports':worker_reports,'report_ops':report_ops,'allowed_sessions':allowed,'main_session_id':main_sid,'sessions':sessions,'entries':entries,'wakes':wakes,'ack_wakes':ack_wakes};normalized=normalize_board_state(stored,branch=sid,repo=state['repo']);idle_key=f"{sid}:idle_workers";idle_seen=dict(ack_wakes.get(idle_key))if isinstance(ack_wakes.get(idle_key),dict)else{};disc_key=f"{sid}:disconnected_workers";disc_seen=dict(ack_wakes.get(disc_key))if isinstance(ack_wakes.get(disc_key),dict)else{};pending_idle_notes=[];pending_disc_notes=[]
		if sid and sid in allowed and normalized.get('board_role')=='main':
			unacked_targets={str(e.get('to')or'')for e in normalized.get('entries')or[]if isinstance(e,dict)and e.get('to')and e.get('to')!='*'and not e.get('acknowledged_at')};active_worker_sids=set()
			for w_row in normalized.get('sessions')or[]:
				w_sid=str(w_row.get('branch')or'')
				if not w_sid or w_sid==sid or not w_row.get('allowed')or w_row.get('role')!='worker':continue
				active_worker_sids.add(w_sid);w_label=str(w_row.get('worker_id')or w_row.get('board_id')or w_sid).strip();is_disc=bool(w_row.get('disconnected')or w_row.get('status')=='disconnected');raw_disc=disc_seen.get(w_sid);prev_disc_seen=str(raw_disc.get('seen')or'')if isinstance(raw_disc,dict)else'';was_disc_active=bool(raw_disc.get('active'))if isinstance(raw_disc,dict)else raw_disc is not None;cur_seen=str(w_row.get('agent_seen_at')or'')
				if is_disc:
					eff_seen=cur_seen or prev_disc_seen;already_disc=was_disc_active and(not cur_seen or not prev_disc_seen or cur_seen<=prev_disc_seen)or not was_disc_active and bool(prev_disc_seen)and(not cur_seen or cur_seen<=prev_disc_seen);disc_seen[w_sid]={'active':True,'seen':eff_seen}
					if not already_disc:pending_disc_notes.append((w_sid,w_label))
				elif raw_disc is not None:
					if not prev_disc_seen or not cur_seen or cur_seen>prev_disc_seen or bool(w_row.get('polling')):disc_seen.pop(w_sid,None)
					else:disc_seen[w_sid]={'active':False,'seen':prev_disc_seen}
				is_idle=bool(not is_disc and w_row.get('polling')and not w_row.get('waiting_for_gates')and not w_row.get('checking_pr')and not wakes.get(w_sid)and w_sid not in unacked_targets);raw_prev=idle_seen.get(w_sid)
				if isinstance(raw_prev,dict):was_active=bool(raw_prev.get('active'));prev_since=str(raw_prev.get('since')or'')
				elif isinstance(raw_prev,str):was_active=True;prev_since=''if raw_prev=='polling'else raw_prev
				else:was_active=False;prev_since=''
				if not is_idle:
					if raw_prev is not None:idle_seen[w_sid]={'active':False,'since':prev_since}
					continue
				episode=str(w_row.get('polling_since')or'');effective_since=episode or prev_since;same_active_episode=was_active and(not episode or not prev_since or episode<=prev_since);same_flapped_episode=not was_active and bool(prev_since)and(not episode or episode<=prev_since)
				if same_active_episode or same_flapped_episode:idle_seen[w_sid]={'active':True,'since':effective_since};continue
				idle_seen[w_sid]={'active':True,'since':effective_since};pending_idle_notes.append((w_sid,w_label))
			for stale_sid in list(idle_seen):
				if stale_sid not in active_worker_sids:idle_seen.pop(stale_sid,None)
			for stale_sid in list(disc_seen):
				if stale_sid not in active_worker_sids:disc_seen.pop(stale_sid,None)
			if idle_seen:ack_wakes[idle_key]=idle_seen
			else:ack_wakes.pop(idle_key,None)
			if disc_seen:ack_wakes[disc_key]=disc_seen
			else:ack_wakes.pop(disc_key,None)
		encoded=json.dumps(stored,ensure_ascii=False)
		if encoded!=raw_text:
			with self.transaction(autosave=False)as db:db.execute('INSERT OR REPLACE INTO meta VALUES (?, ?)',(BOARD_STATE_META,encoded))
		for(w_sid,w_label)in pending_idle_notes:
			with closing(self.connect())as db:dup=db.execute("SELECT 1 FROM notes WHERE type = 'worker-idle' AND acknowledged_at IS NULL AND instr(text, ?) > 0 LIMIT 1",(f"({w_sid})",)).fetchone()
			if dup is not None:continue
			tag=w_label if w_label.startswith('@')else f"@{w_label}";self.note(self.free_id(),f"Worker {tag} ({w_sid}) is idle in poll. Delegate an independent task or ack this note.",quiet=True,blocking=True,type='worker-idle')
		for(w_sid,w_label)in pending_disc_notes:
			with closing(self.connect())as db:dup=db.execute("SELECT 1 FROM notes WHERE type = 'worker-disconnected' AND acknowledged_at IS NULL AND instr(text, ?) > 0 LIMIT 1",(f"({w_sid})",)).fetchone()
			if dup is not None:continue
			tag=w_label if w_label.startswith('@')else f"@{w_label}";self.note(self.free_id(),f"Worker {tag} ({w_sid}) disconnected from the board. Reassign its task or ack this note.",quiet=True,blocking=True,type='worker-disconnected')
		if payload.get('reply_mode')=='diff'and payload.get('reply_since')is not None:return self.board_state_sync(since=int(payload['reply_since']),mode='diff')
		if payload.get('client_rev')is not None or isinstance(payload.get('board_diff'),dict):return self.board_state_sync(mode='full')
		return self.board_state()
	def tasks(self):
		with closing(self.connect())as db:
			rows=db.execute(f"SELECT {TASK_COLUMNS} FROM tasks ORDER BY {TASK_ORDER}").fetchall();records=attach_blockers(db,[task_row(row)for row in rows])
			if not records:return None
			link_task_answers(db,records)
		link_task_runs(records,self.path.parent);return{'finished':[item for item in records if item['status']=='finished'],'upcoming':[item for item in records if item['status']=='upcoming'],'updated_at':max(item['updated_at']for item in records)}
	def list_tasks(self):
		with closing(self.connect())as db:rows=db.execute(f"SELECT {TASK_COLUMNS} FROM tasks ORDER BY {TASK_ORDER}").fetchall();records=link_task_answers(db,attach_blockers(db,[task_row(row)for row in rows]))
		return link_task_runs(records,self.path.parent)
	def task_record(self,task_id):
		with closing(self.connect())as db:row=db.execute(f"SELECT {TASK_COLUMNS} FROM tasks WHERE id = ?",(task_id,)).fetchone()
		if row is None:return None
		with closing(self.connect())as db:record=attach_blockers(db,[task_row(row)])[0]
		return link_task_runs([record],self.path.parent)[0]
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
	def task_blockers(self,task_id,db=None):
		with self.transaction(db,autosave=False)as conn:rows=conn.execute('SELECT blocked_by FROM task_dependencies WHERE task_id = ? ORDER BY blocked_by',(task_id,)).fetchall()
		return[row[0]for row in rows]
	def all_task_blockers(self,db=None):
		edges={}
		with self.transaction(db,autosave=False)as conn:rows=conn.execute('SELECT task_id, blocked_by FROM task_dependencies ORDER BY blocked_by').fetchall()
		for row in rows:edges.setdefault(row[0],[]).append(row[1])
		return edges
	def check_dependency(self,task_id,blocker,edges,db=None):
		if blocker==task_id:raise ValueError(f"{task_id} cannot wait on itself")
		with self.transaction(db,autosave=False)as conn:exists=conn.execute('SELECT 1 FROM tasks WHERE id = ?',(blocker,)).fetchone()
		if not exists:raise ValueError(f"No task holds {blocker}; add it before another waits on it")
		pending=[blocker];seen=set()
		while pending:
			node=pending.pop()
			if node==task_id:raise ValueError(f"{blocker} already waits on {task_id}; that edge would close a cycle")
			if node in seen:continue
			seen.add(node);pending.extend(edges.get(node,[]))
	def write_dependencies(self,db,task_id,blocked_by):
		db.execute('DELETE FROM task_dependencies WHERE task_id = ?',(task_id,))
		for blocker in blocked_by:db.execute('INSERT OR IGNORE INTO task_dependencies (task_id, blocked_by) VALUES (?, ?)',(task_id,blocker))
	def refuse_session_ids(self,details,db=None):
		tokens={token:found for(token,found)in session_id_tokens(self,db=db).items()if NOTE_ID.fullmatch(token)or HEX7_RE.fullmatch(token)}
		for detail in details:
			for(token,(type,ident))in tokens.items():
				if re.search(f"(?<![0-9A-Za-z]){re.escape(token)}(?![0-9A-Za-z])",detail):named=ident if token==ident else f"{token} of {ident}";raise ValueError(f"That detail cites the session-local {type} {named}. A repository reader cannot resolve that ID. Describe the work instead")
	def write_task(self,task_id,title=None,details=None,status=None,order=None,blocked=None,report_id=None,completed_at=None,task_type=None,blocked_by=None,shared=None,append_details=False,restoring=False,scope=None):
		details=[str(item)for item in details if str(item).strip()]if details else None;scope_line=None
		if scope is not None:
			if not isinstance(scope,str)or'\n'in scope or'\r'in scope or not scope.strip():raise ValueError('A task scope must be a non-empty single line of text')
			scope_line=f"{TASK_SCOPE_PREFIX}{scope.strip()}";check_task_steps([scope_line])
		if details and not restoring:self.refuse_session_ids(details,db=shared)
		if scope_line and not restoring:self.refuse_session_ids([scope_line],db=shared)
		if append_details and not details:raise ValueError('Appending requires at least one task detail')
		check_task(task_id,title,details)
		if status is not None and status not in TASK_STATUSES:raise ValueError(f"A task is either {' or '.join(TASK_STATUSES)}")
		allowed_types=TASK_TYPES+TASK_LEGACY_TYPES if restoring else TASK_TYPES
		if task_type is not None and task_type not in allowed_types:raise ValueError(f"A task type is one of {', '.join(allowed_types)}")
		stamp=now()
		with self.transaction(shared)as db:
			if append_details and not db.in_transaction:db.execute('BEGIN IMMEDIATE')
			row=db.execute(f"SELECT {TASK_COLUMNS} FROM tasks WHERE id = ?",(task_id,)).fetchone();stored=task_row(row)if row else None
			if stored is None and title is None:raise ValueError('A new task needs a title')
			if stored is None:self.refuse_shared_id(db,'tasks','reports',task_id)
			title=title if title is not None else stored['title'];stored_details=list(stored['details'])if stored else[];stored_scope=stored_details[0]if stored_details and stored_details[0].startswith(TASK_SCOPE_PREFIX)else None
			if append_details:
				if scope_line is not None:
					if stored_scope is not None:stored_details[0]=scope_line
					else:stored_details.insert(0,scope_line)
				details=stored_details+details;check_task(task_id,title,details)
			elif details is None:
				if scope_line is not None:
					if stored_scope is not None:stored_details[0]=scope_line
					else:stored_details.insert(0,scope_line)
					check_task(task_id,title,stored_details)
				details=stored_details
			else:
				if scope_line is not None:
					if details and details[0].startswith(TASK_SCOPE_PREFIX):details=[scope_line,*details[1:]]
					else:details=[scope_line,*details]
				elif stored_scope is not None and not(details and details[0].startswith(TASK_SCOPE_PREFIX)):details=[stored_scope,*details]
				check_task(task_id,title,details)
			if not details:raise ValueError('A task needs at least one detail')
			status_arg=status;status=status or(stored['status']if stored else'upcoming')
			if status=='finished':
				if stored and stored['status']=='finished':completed_at=stored['completed_at']
				elif completed_at is None:completed_at=stamp
			else:completed_at=None
			if blocked is None:blocked=stored['blocked']if stored else False
			report_wait=report_id is not None
			if report_id is None:report_id=stored.get('report_id')if stored else None
			if task_type is None:
				if stored is None and not restoring:raise ValueError('A new task needs an explicit type')
				task_type=(stored.get('type')if stored else None)or TASK_MISC
			if report_id:identifier(report_id)
			if blocked_by is not None:
				blocked_by=[str(item)for item in blocked_by if str(item).strip()];edges=self.all_task_blockers(db=db)
				for blocker in blocked_by:self.check_dependency(task_id,blocker,edges,db=db)
				self.write_dependencies(db,task_id,blocked_by)
			if blocked and(report_wait or status=='finished')and self.task_has_report_answer(db,task_id,report_id):blocked=False
			if report_id and blocked and(not stored or not stored['blocked']or stored.get('report_id')!=report_id):
				report=db.execute('SELECT markdown FROM reports WHERE id = ?',(report_id,)).fetchone()
				if report is not None and not parse_fields(report['markdown'])[1]:raise ValueError('A blocked task needs a report with answer fields')
			if status=='finished':
				guard_task_runs(self.path.parent,task_id,'finish task')
				if not restoring and self.board_state(polling=False).get('board_role')!='worker'and(status_arg=='finished'or not stored or stored['status']!='finished'):guard_task_push(self.repository_root(),task_id,task_type)
			if status=='finished'and blocked:raise ValueError(f"Task {task_id} is blocked; clear the report wait with --unblocked first")
			siblings=[task_row(item)for item in db.execute(f"SELECT {TASK_COLUMNS} FROM tasks WHERE status = ? AND id != ? ORDER BY position, id",(status,task_id)).fetchall()]
			if not restoring and order is not None and not blocked and status=='upcoming':siblings=[item for item in siblings if not(item.get('blocked')and item.get('report_id'))]+[item for item in siblings if item.get('blocked')and item.get('report_id')]
			record={'id':task_id,'title':title,'details':details,'status':status,'order':len(siblings)+1,'updated_at':stamp,'completed_at':completed_at,'blocked':bool(blocked),'report_id':report_id,'type':task_type};blockers=self.task_blockers(task_id,db=db)
			if blockers:record['blocked_by']=blockers
			if order is not None:index=max(0,min(order-1,len(siblings)))
			elif stored and stored['status']==status:index=max(0,min(stored['order']-1,len(siblings)))
			else:index=len(siblings)
			if not restoring and stored and stored['status']==status=='upcoming'and order is not None and order!=1 and index+1!=stored['order']and meta_number(db,TASK_ORDER_OWNED_META):raise ValueError('Task order belongs to the preview; omit --order or pass --order 1')
			siblings.insert(index,record);record['order']=index+1;db.execute('INSERT INTO tasks (id, title, details, status, position, created_at, updated_at, completed_at, blocked, report_id, type) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET title = excluded.title, details = excluded.details, status = excluded.status, position = excluded.position, updated_at = excluded.updated_at, completed_at = excluded.completed_at, blocked = excluded.blocked, report_id = excluded.report_id, type = excluded.type',(task_id,title,json.dumps(details,ensure_ascii=False),status,index+1,stamp,stamp,completed_at,1 if blocked else 0,report_id,task_type))
			for(position,item)in enumerate(siblings,1):db.execute('UPDATE tasks SET position = ? WHERE id = ?',(position,item['id']))
			self.renumber(db)
		return link_task_runs([record],self.path.parent)[0]
	def reorder_tasks(self,ids):
		if not isinstance(ids,list)or not ids or not all(isinstance(item,str)for item in ids):raise ValueError('ids must be a list of one or more task IDs')
		if len(set(ids))!=len(ids):raise ValueError('Each task ID appears once in ids')
		with self.transaction()as db:
			rows=db.execute('SELECT id FROM tasks WHERE status = ? ORDER BY position, id',('upcoming',)).fetchall();stored=[row['id']for row in rows]
			for task_id in ids:
				if task_id not in stored:raise ValueError(f"No upcoming task holds {task_id}")
			db.executemany('INSERT OR REPLACE INTO meta VALUES (?, ?)',[(TASK_ORDER_OWNED_META,'1'),(TASK_ORDER_PENDING_META,'1')]);drawn=iter(ids);wanted=set(ids);sequence=[next(drawn)if task_id in wanted else task_id for task_id in stored]
			for(position,task_id)in enumerate(sequence,1):db.execute('UPDATE tasks SET position = ? WHERE id = ?',(position,task_id))
			db.execute(f"UPDATE tasks SET updated_at = ? WHERE id IN ({', '.join('?'for _ in ids)})",(now(),*ids))
		return self.tasks()
	@staticmethod
	def report_has_answers(db,report_id):return bool(db.execute('SELECT 1 FROM submissions WHERE report_id = ? LIMIT 1',(report_id,)).fetchone())
	def task_has_report_answer(self,db,task_id,report_id=None):
		if report_id:return self.report_has_answers(db,report_id)
		return bool(db.execute('SELECT 1 FROM submissions WHERE task_id = ? LIMIT 1',(task_id,)).fetchone())
	@staticmethod
	def release_report_tasks(db,report_id):db.execute('UPDATE tasks SET blocked = 0, updated_at = ? WHERE report_id = ? AND blocked = 1',(now(),report_id))
	def remove_task(self,task_id):
		guard_task_runs(self.path.parent,task_id,'remove task')
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
		type=exact[0][0];table,dependents=RENAME_TARGETS[type]
		if type=='task':guard_task_runs(self.path.parent,prev_id,'rename task');check_task(new_id,None,None)
		with self.transaction()as db:
			for other in RENAME_TABLES:
				if db.execute(f"SELECT 1 FROM {other} WHERE id = ?",(new_id,)).fetchone():raise ValueError(f"{new_id} already names a row in {other}")
			db.execute(f"UPDATE {table} SET id = ? WHERE id = ?",(new_id,prev_id))
			if table=='tasks':db.execute('UPDATE tasks SET updated_at = ? WHERE id = ?',(now(),new_id))
			for(other,column)in dependents:db.execute(f"UPDATE {other} SET {column} = ? WHERE {column} = ?",(new_id,prev_id))
		return type
	def amend_task(self,prev_id,task_id):
		check_task(task_id,None,None);stamp=now()
		with self.transaction()as db:
			row=db.execute('SELECT id, title, details, status, position, created_at, completed_at, blocked, report_id, type FROM tasks WHERE id = ?',(prev_id,)).fetchone()
			if row is None:raise ValueError(f"No task is stored under {prev_id}")
			guard_task_runs(self.path.parent,prev_id,'rename task')
			if db.execute('SELECT 1 FROM tasks WHERE id = ?',(task_id,)).fetchone():raise ValueError(f"A task is already stored under {task_id}")
			self.refuse_shared_id(db,'tasks','reports',task_id);db.execute('DELETE FROM tasks WHERE id = ?',(prev_id,));db.execute('INSERT INTO tasks (id, title, details, status, position, created_at, updated_at, completed_at, blocked, report_id, type) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',(task_id,row[1],row[2],row[3],row[4],row[5],stamp,row[6],row[7],row[8],row[9]));self.renumber(db)
	def userscript_history(self,shared=None):
		with self.transaction(shared,autosave=False)as db:row=db.execute('SELECT value FROM meta WHERE key = ?',(AUTOSAVE_HISTORY,)).fetchone();return json.loads(row[0])if row else[]
	def userscript_scope(self):root=self.path.parent.parent;return{'repo':root.name,'branch':workspace_branch(root)or''}
	def request_userscript_force_save(self,shared=None):
		save_id=str(uuid.uuid4())
		with self.transaction(shared,autosave=False)as db:db.execute('INSERT OR REPLACE INTO meta VALUES (?, ?)',(FORCE_SAVE_META,save_id))
		return save_id
	def userscript_snapshot(self,since=None,mode='full'):
		core,counts=self.state_lines(self.state()['notes'],self.tasks()or{});core,stamp=autosave_core_and_stamp(core);scope=self.userscript_scope();history=self.userscript_history();stamps=self.newest_stamps();rows=[row for row in history if row['scope']==scope and row.get('stamp')];force_save_id=self.meta_value(FORCE_SAVE_META);instance_id=getattr(self,'_userscript_instance_id',None)
		if instance_id is None:instance_id=str(uuid.uuid4());self._userscript_instance_id=instance_id
		save_id=force_save_id if force_save_id else str(uuid.uuid4());digest=autosave_digest(core);lastsave=rows[-1]['stamp']if rows else None;beforelastsave=rows[-2]['stamp']if len(rows)>1 else None;lock=getattr(self,'_autosave_sync_lock',None)
		if lock is None:lock=threading.RLock();self._autosave_sync_lock=lock;self._autosave_sync_rev=0;self._autosave_sync_key=None;self._autosave_sync_history={}
		with lock:
			self._autosave_latest=digest,core;sync_key=digest,lastsave,beforelastsave,bool(force_save_id),json.dumps(scope,sort_keys=True)
			if self._autosave_sync_rev==0 or sync_key!=self._autosave_sync_key:
				self._autosave_sync_rev+=1;self._autosave_sync_key=sync_key;self._autosave_sync_history[self._autosave_sync_rev]=core;cutoff=self._autosave_sync_rev-8
				for rev_key in[k for k in self._autosave_sync_history if k<cutoff]:self._autosave_sync_history.pop(rev_key,None)
			cur_rev=self._autosave_sync_rev
			if mode=='diff'and not force_save_id and since is not None:
				base_fields={'revision':cur_rev,'mode':'diff','save_id':save_id,'force':False,'instance_id':instance_id,'scope':scope,'digest':digest,'lastsave':lastsave,'beforelastsave':beforelastsave,'counts':counts,'stamp':stamp,'stamps':stamps}
				if since==cur_rev:return{**base_fields,'unchanged':True}
				if since<cur_rev and since in self._autosave_sync_history:return{**base_fields,'unchanged':False,'diff':compute_preview_state_diff(self._autosave_sync_history[since],core,since,cur_rev)}
		return{'revision':cur_rev,'save_id':save_id,'force':bool(force_save_id),'instance_id':instance_id,'scope':scope,'core':core,'digest':digest,'history':rows,'lastsave':lastsave,'beforelastsave':beforelastsave,'text':state_ndjson(core+rows),'counts':counts,'stamp':stamp,'stamps':stamps}
	def confirm_userscript_save(self,payload):
		save_id,scope=payload.get('save_id'),payload.get('scope')
		if not isinstance(save_id,str):raise TypeError('Autosave save ID must be a UUID string')
		uuid.UUID(save_id);key=autosave_scope_key(scope)
		if scope!=self.userscript_scope():raise ValueError('Autosave confirmation is outside this repository and branch')
		if'core'in payload:core=autosave_core(payload.get('core'))
		else:
			latest=getattr(self,'_autosave_latest',None)
			if latest is not None and latest[0]==payload.get('digest'):core=latest[1]
			else:live_lines,_=self.state_lines(self.state()['notes'],self.tasks()or{});core=autosave_core(live_lines)
		stamp=payload.get('stamp',payload.get('lastsave'))
		if stamp is not None and import_stamp(stamp)is None:raise ValueError('Autosave confirmation stamp is invalid')
		digest=autosave_digest(core)
		if digest!=payload.get('digest'):raise ValueError('Autosave confirmation digest does not match')
		with self.transaction()as db:
			db.execute('BEGIN IMMEDIATE');row=db.execute('SELECT value FROM meta WHERE key = ?',(AUTOSAVE_RECEIPTS,)).fetchone();receipts=json.loads(row[0])if row else{};scope_receipts=receipts.setdefault(key,{})
			if save_id in scope_receipts:
				if scope_receipts[save_id]!=digest:raise ValueError('Autosave save ID already confirms another state')
			else:
				scope_receipts[save_id]=digest;history=self.userscript_history(db);record={'type':'userscript-autosave','version':1,'save_id':save_id,'scope':scope,'digest':digest,'core':core,'stamp':stamp,'confirmed_at':now()};previous=[item for item in history if item['scope']==scope];kept=previous[-1:]+[record]
				for item in kept:item['receipts']=scope_receipts.copy()
				history=[item for item in history if item['scope']!=scope]+kept;db.execute('INSERT OR REPLACE INTO meta VALUES (?, ?)',(AUTOSAVE_HISTORY,json.dumps(history)));db.execute('INSERT OR REPLACE INTO meta VALUES (?, ?)',(AUTOSAVE_RECEIPTS,json.dumps(receipts)));db.execute('DELETE FROM meta WHERE key = ?',(AUTOSAVE_OFFER_META,))
			db.execute('DELETE FROM meta WHERE key = ?',(FORCE_SAVE_META,))
		return{'confirmed':True,'save_id':save_id,'digest':digest}
	def offer_userscript_backup(self,payload):
		if not isinstance(payload,dict):raise TypeError('Expected a backup offer object')
		text=payload.get('text')
		if not isinstance(text,str)or not text.strip():raise ValueError('Backup offer text must be a nonblank string')
		board=self.board_state()
		if board.get('main_session_id')and board.get('board_role')=='worker':return{'offered':False,'duplicate':False,'worker':True}
		records=parse_state_import(text);raw_name=payload.get('name');name=upload_name(raw_name if isinstance(raw_name,str)and raw_name.strip()else'state-backup.ndjson')
		if not name.lower().endswith(('.ndjson','.json')):name=f"{name}.ndjson"
		summary=payload.get('summary');summary_text=summary.strip()if isinstance(summary,str)and summary.strip()else'State mismatch detected.';data=text.encode('utf-8');backup_digest=body_digest(data);live_core,_=self.state_lines(self.state()['notes'],self.tasks()or{});live_digest=autosave_digest(live_core);mismatch_key=f"{backup_digest}:{live_digest}";backup_ids={str(row.get('id'))for row in records if isinstance(row,dict)and row.get('id')};created=[]
		try:
			with self.transaction(autosave=False)as db:
				db.execute('BEGIN IMMEDIATE');offered=db.execute('SELECT value FROM meta WHERE key = ?',(AUTOSAVE_OFFER_META,)).fetchone();pending=db.execute("SELECT id FROM notes WHERE type = 'state-backup' AND acknowledged_at IS NULL ORDER BY at DESC, seq DESC LIMIT 1").fetchone()
				if offered and offered[0]==mismatch_key:last_note=pending or db.execute("SELECT id FROM notes WHERE type = 'state-backup' ORDER BY at DESC, seq DESC LIMIT 1").fetchone();upload=db.execute('SELECT * FROM uploads WHERE note_id = ? ORDER BY seq LIMIT 1',(last_note['id'],)).fetchone()if last_note is not None else None;return{'offered':True,'duplicate':True,'note_id':last_note['id']if last_note is not None else None,'path':upload_row(upload,self.path.parent)['path']if upload else None}
				for _ in range(MINT_ATTEMPTS):
					note_id=new_id()
					if note_id not in backup_ids and not any(db.execute(f"SELECT 1 FROM {table} WHERE id = ?",(note_id,)).fetchone()for table in('notes','submissions','uploads','reports','tasks')):break
				else:raise RuntimeError('The state holds no free six-character ID for a backup note')
				record=self.save_upload(name,'application/x-ndjson',data,upload_id=note_id,note_id=note_id,shared=db);created.append(Path(record['path']));note_body=f"{summary_text} Backup saved to {record['path']}. Run arena-preview state compare, import with -r if newer, then ack this note.";note=self.note(note_id,note_body,shared=db,autosave=False,quiet=True,blocking=True,type='state-backup');db.execute('INSERT OR REPLACE INTO meta VALUES (?, ?)',(AUTOSAVE_OFFER_META,mismatch_key))
		except Exception:
			for path in created:path.unlink(missing_ok=True)
			raise
		return{'offered':True,'duplicate':False,'note_id':note['id'],'path':record['path']}
	def compare_userscript_save(self,payload):
		candidate,candidate_stamp=autosave_core_and_stamp(payload.get('core'));baseline=payload.get('baseline');result={'candidate_stamp':candidate_stamp,'counts':{'candidate':len(candidate)}}
		if not isinstance(baseline,dict):return{**result,'result':'unknown-baseline'}
		if payload.get('scope')!=baseline.get('scope')or payload.get('scope')!=self.userscript_scope():return{**result,'result':'scope-mismatch'}
		rows=[row for row in self.userscript_history()if row['scope']==baseline['scope']and row.get('stamp')is not None];lastsave=rows[-1]['stamp']if rows else None;beforelastsave=rows[-2]['stamp']if len(rows)>1 else None;slots={'lastsave':lastsave,'beforelastsave':beforelastsave};result={**result,**slots}
		if baseline.get('lastsave')!=lastsave or baseline.get('beforelastsave')!=beforelastsave or not rows:return{**result,'result':'unknown-baseline'}
		saved=rows[-1];supplied=baseline.get('core')
		if supplied is not None and autosave_core(supplied)!=autosave_core(saved['core']):return{**result,'result':'unknown-baseline'}
		core=autosave_core(saved['core']);before=saved['stamp']
		def identity(row):type='report'if'markdown'in row else'task'if'title'in row else'answer'if'report_id'in row else'note';return type,row['id']
		missing=len({identity(row)for row in core}-{identity(row)for row in candidate});status='equal'if candidate_stamp==before else'candidate-missing-records'if missing else'different';return{**result,'result':status,'baseline_stamp':before,'counts':{'baseline':len(core),'candidate':len(candidate),'missing':missing}}
	def autosave(self):
		try:self.save_state({'notes':self.state()['notes'],'tasks':self.tasks()})
		except Exception as error:print(f"preview: autosave failed: {error}",file=sys.stderr)
	def state_lines(self,notes,tasks):
		if not isinstance(notes,list)or not isinstance(tasks,dict):raise TypeError('Save a state object with notes and tasks')
		saved_notes=[record for record in notes if not record.get('quiet')];lines=[saved_note_line(record)for record in saved_notes]
		for status in TASK_STATUSES:
			for record in tasks.get(status)or[]:lines.append(saved_task_line(record))
		sources=self.report_sources();live={record['id']for record in sources};task_ids=set();task_reports=set()
		for status in TASK_STATUSES:
			for record in(tasks.get(status)if tasks is not None else None)or[]:
				task_ids.add(record.get('id'))
				if record.get('report_id'):task_reports.add(record['report_id'])
		answers=[saved_answer_line(record)for record in self.submissions()if record.get('report_id')in live or record.get('task_id')in task_ids or record.get('task_id')==UNLINKED_TASK_ID or record.get('report_id')in task_reports];lines.extend(answers);reports=[saved_report_line(record)for record in sources];lines.extend(reports);return lines,{'notes':len(saved_notes),'tasks':len(lines)-len(saved_notes)-len(answers)-len(reports),'answers':len(answers),'reports':len(reports)}
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
	def rotate_backup(self):
		text=''
		if self.latest_backup_path.is_file():
			try:text=self.latest_backup_path.read_text(encoding='utf-8')
			except OSError:text=''
		if not text.strip():
			lines,counts=self.state_lines(self.state()['notes'],self.tasks()or{})
			if any(counts.values()):text=state_ndjson(lines+self.userscript_history())
		if text.strip():self.previous_backup_path.parent.mkdir(parents=True,exist_ok=True);self.previous_backup_path.write_text(text,encoding='utf-8');return True
		return False
	def save_state(self,payload):
		if not isinstance(payload,dict):raise TypeError('Save a state object')
		notes=payload.get('notes');tasks=payload.get('tasks')
		if tasks is None:tasks={}
		lines,counts=self.state_lines(notes,tasks);text=state_ndjson(lines+self.userscript_history());path=self.save_path
		if path.parent!=Path('.'):path.parent.mkdir(parents=True,exist_ok=True)
		path.write_text(text,encoding='utf-8');self.latest_backup_path.parent.mkdir(parents=True,exist_ok=True);self.latest_backup_path.write_text(text,encoding='utf-8')
		if any(counts.values())and self.meta_value(CLEARED_META)=='1':
			with closing(self.connect())as db,db:db.execute('DELETE FROM meta WHERE key = ?',(CLEARED_META,))
		return{'path':str(path),'latest_path':str(self.latest_backup_path),**counts}
	def save_backup(self):result=self.save_state({'notes':self.state()['notes'],'tasks':self.tasks()});self.request_userscript_force_save();return result
	def restore_state(self,previous=False):
		candidates=(self.previous_backup_path,)if previous else(self.latest_backup_path,self.previous_backup_path,self.save_path);chosen=None;text=''
		for candidate in candidates:
			if not candidate.is_file():continue
			try:raw=candidate.read_text(encoding='utf-8')
			except OSError:continue
			if raw.strip():chosen=candidate;text=raw;break
		if chosen is None:label='previous backup'if previous else'backup';raise ValueError(f"No non-empty {label} file found to restore")
		receipt=self.import_state(text,replace_tasks=True,rotate=not previous);self.request_userscript_force_save();self.autosave();return receipt
	def uploads(self):
		with closing(self.connect())as db:rows=db.execute('SELECT * FROM uploads WHERE note_id IS NOT NULL ORDER BY seq').fetchall()
		return[upload_row(row,self.path.parent)for row in rows]
	def save_upload(self,name,content_type,data,upload_id=None,shared=None,note_id=None,position=None,staged=False):
		if not isinstance(data,(bytes,bytearray,FileBody)):raise TypeError('An upload is bytes')
		if not data:raise ValueError('An upload must not be empty')
		cleaned=upload_name(name);type=upload_type(content_type);digest=body_digest(data);upload_id=identifier(upload_id)if upload_id is not None else self.free_id()
		if note_id is not None:note_id=identifier(note_id)
		elif not staged:note_id=upload_id
		if position is not None and(not isinstance(position,int)or position<1):raise ValueError('Invalid attachment position')
		directory=self.path.parent/UPLOAD_DIR;directory.mkdir(parents=True,exist_ok=True,mode=448)
		with self.transaction(shared)as db:
			if shared is None:db.execute('BEGIN IMMEDIATE')
			existing=db.execute('SELECT * FROM uploads WHERE id = ?',(upload_id,)).fetchone()
			if existing:
				if(existing['note_id'],existing['name'],existing['type'],existing['size'],existing['sha256'])!=(note_id,cleaned,type,len(data),digest):raise ValueError('This note ID already belongs to a different file')
				target=directory/existing['file']
				if not target.is_file()or hashlib.sha256(target.read_bytes()).hexdigest()!=digest:save_body(target,data);target.chmod(384)
				return upload_row(existing,self.path.parent)
			duplicate_count=db.execute("SELECT COUNT(*) FROM uploads WHERE note_id = ? AND replace(name, ' ', '-') = ?",(note_id,cleaned.replace(' ','-'))).fetchone()[0];number=duplicate_count+1;timestamp=int(time.time());extension=Path(cleaned).suffix[:16].replace(' ','-');filename_stem=Path(cleaned).stem.replace(' ','-')
			while True:
				duplicate=f"-{number}"if number>1 else'';stem=f"{(note_id or upload_id)[:6]}-{timestamp}{duplicate}-{filename_stem}";target=directory/f"{stem}{extension}"
				if len(os.fsencode(target.name))>255:target=directory/f"{upload_id}{duplicate}{extension}"
				collision=db.execute('SELECT 1 FROM uploads WHERE file = ?',(target.name,)).fetchone()
				if not collision and not target.exists():break
				number+=1
			save_body(target,data);target.chmod(384);db.execute('INSERT INTO uploads (id, note_id, name, type, size, sha256, file, at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)',(upload_id,note_id,cleaned,type,len(data),digest,target.name,now()));row=db.execute('SELECT * FROM uploads WHERE id = ?',(upload_id,)).fetchone()
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
					if len(rows)!=len(prepared)or any((row['name'],row['type'],row['size'],row['sha256'])!=(name,type,len(data),body_digest(data))for(row,(name,type,data))in zip(rows,prepared)):raise ValueError('This note ID already belongs to different files')
					for(row,(_,_,data))in zip(rows,prepared):
						target=self.path.parent/UPLOAD_DIR/row['file']
						if not target.is_file()or hashlib.sha256(target.read_bytes()).hexdigest()!=row['sha256']:target.parent.mkdir(parents=True,exist_ok=True,mode=448);save_body(target,data);target.chmod(384)
					records=[upload_row(row,self.path.parent)for row in rows]
				else:
					if existing:raise ValueError('This note ID was already sent without a file')
					records=[]
					for(index,(name,type,data))in enumerate(prepared,1):record=self.save_upload(name,type,data,upload_id=note_id if index==1 else new_id(),note_id=note_id,position=index if len(prepared)>1 else None,shared=db);records.append(record);created.append(Path(record['path']))
				note=self.note(note_id,text,shared=db);result=add_note_attachments(note,records)
		except Exception:
			for path in created:path.unlink(missing_ok=True)
			raise
		self.autosave();return result
	def stage_upload(self,name,content_type,data,upload_id):record=self.save_upload(name,content_type,data,upload_id=upload_id,staged=True);self.autosave();return record
	def drop_staged(self,upload_id):
		upload_id=identifier(upload_id)
		with self.transaction()as db:
			row=db.execute('SELECT * FROM uploads WHERE id = ?',(upload_id,)).fetchone()
			if row is None:raise ValueError('No staged file is stored under this ID')
			if row['note_id']is not None:raise ValueError('This file already belongs to a note')
			db.execute('DELETE FROM uploads WHERE id = ?',(upload_id,))
		(self.path.parent/UPLOAD_DIR/row['file']).unlink(missing_ok=True);self.autosave();return{'dropped':upload_id}
	def note_with_staged(self,note_id,text,upload_ids):
		identifier(note_id);owner_text(text)
		if not isinstance(upload_ids,(list,tuple))or not upload_ids:raise ValueError('Attach at least one staged file to a note')
		ids=[identifier(upload_id)for upload_id in upload_ids]
		if len(set(ids))!=len(ids):raise ValueError('Each staged file may attach once')
		with self.transaction(autosave=False)as db:
			db.execute('BEGIN IMMEDIATE');existing=db.execute('SELECT text FROM notes WHERE id = ?',(note_id,)).fetchone()
			if existing and existing['text']!=text:raise ValueError('This message ID already belongs to different text')
			rows=[db.execute('SELECT * FROM uploads WHERE id = ?',(upload_id,)).fetchone()for upload_id in ids]
			if any(row is None for row in rows):raise ValueError('No staged file is stored under this ID')
			if any(row['note_id']is not None for row in rows):
				if existing and all(row['note_id']==note_id for row in rows):note=self.note(note_id,text,shared=db);return add_note_attachments(note,[upload_row(row,self.path.parent)for row in rows])
				raise ValueError('A staged file already belongs to a different note')
			if existing:raise ValueError('This message ID was already sent without a file')
			for row in rows:db.execute('UPDATE uploads SET note_id = ? WHERE id = ?',(note_id,row['id']))
			rows=[db.execute('SELECT * FROM uploads WHERE id = ?',(upload_id,)).fetchone()for upload_id in ids];note=self.note(note_id,text,shared=db);result=add_note_attachments(note,[upload_row(row,self.path.parent)for row in rows])
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
			job_id=row['id'];claim=secrets.token_urlsafe(24);lease=(datetime.now(UTC)+FETCH_LEASE).isoformat();db.execute("UPDATE fetch_jobs SET status = 'fetching', claim = ?, lease_until = ?, error = NULL, updated_at = ? WHERE id = ?",(claim,lease,stamp,job_id));row=db.execute('SELECT * FROM fetch_jobs WHERE id = ?',(job_id,)).fetchone()
		return fetch_row(row,self.path.parent)|{'claim':claim}
	def claimed_fetch(self,db,job_id,claim):
		identifier(job_id);row=db.execute('SELECT * FROM fetch_jobs WHERE id = ?',(job_id,)).fetchone()
		if row is None:raise FileNotFoundError('No queued download with that ID')
		if row['status']!='fetching'or row['approval']!='approved'or not isinstance(claim,str)or not secrets.compare_digest(row['claim']or'',claim)or not row['lease_until']or row['lease_until']<=now():raise FetchChanged('Download claim expired; refresh the queue and try again')
		return row
	def renew_fetch(self,job_id,claim):
		with self.transaction(autosave=False)as db:self.claimed_fetch(db,job_id,claim);lease=(datetime.now(UTC)+FETCH_LEASE).isoformat();db.execute('UPDATE fetch_jobs SET lease_until = ? WHERE id = ?',(lease,job_id));row=db.execute('SELECT * FROM fetch_jobs WHERE id = ?',(job_id,)).fetchone()
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
				if source!='direct'and not row['allow_proxy']:raise ValueError('Proxy fallback was not enabled for this URL')
				directory=self.path.parent/FETCH_DIR;directory.mkdir(parents=True,exist_ok=True,mode=448);target=directory/f"{job_id}{Path(cleaned).suffix[:16]}";save_body(target,data);target.chmod(384);stamp=now();db.execute("UPDATE fetch_jobs SET status = 'saved', source = ?, name = ?, type = ?, size = ?, sha256 = ?, file = ?, error = NULL, claim = NULL, lease_until = NULL, updated_at = ? WHERE id = ?",(source,cleaned,file_type,len(data),body_digest(data),target.name,stamp,job_id));message=f"Download: {cleaned} ({len(data)} B, {file_type}) from {urlsplit(row['url']).hostname} via {source} saved to {target}";db.execute('INSERT INTO notes (id, text, at) VALUES (?, ?, ?)',(job_id,message,stamp));result=db.execute('SELECT * FROM fetch_jobs WHERE id = ?',(job_id,)).fetchone()
		except Exception:
			if target is not None:target.unlink(missing_ok=True)
			raise
		self.autosave();return fetch_row(result,self.path.parent)
	def import_state(self,text,replace_tasks=False,rotate=True):
		records=parse_state_import(text)
		if rotate and self.latest_backup_path.is_file():
			try:prior=self.latest_backup_path.read_text(encoding='utf-8')
			except OSError:prior=''
			if prior.strip():self.previous_backup_path.parent.mkdir(parents=True,exist_ok=True);self.previous_backup_path.write_text(prior,encoding='utf-8')
		raw_history=[row for row in records if row.get('type')=='userscript-autosave'];history=[autosave_record(row)for row in raw_history];refreshed={raw_row['save_id']:(raw_row['digest'],row['digest'])for(raw_row,row)in zip(raw_history,history)if raw_row.get('digest')!=row['digest']}
		for row in history:
			if'receipts'in row:row['receipts']={save_id:refreshed[save_id][1]if save_id in refreshed and digest==refreshed[save_id][0]else digest for(save_id,digest)in row['receipts'].items()}
		records=[row for row in records if row.get('type')!='userscript-autosave'];groups={};imported_receipts={}
		for row in history:
			key=autosave_scope_key(row['scope']);groups.setdefault(key,[]).append(row);receipts=imported_receipts.setdefault(key,{})
			for(save_id,digest)in{**row.get('receipts',{}),row['save_id']:row['digest']}.items():
				if save_id in receipts and receipts[save_id]!=digest:raise ValueError('Conflicting autosave receipt digests')
				receipts[save_id]=digest
			if len(groups[key])>2 or len({item['save_id']for item in groups[key]})!=len(groups[key]):raise ValueError('Autosave history holds at most two distinct saves per scope')
		reports=[r for r in records if'markdown'in r and'text'not in r];tasks=[r for r in records if'markdown'not in r and'title'in r and'text'not in r];messages=[r for r in records if'markdown'not in r and('title'not in r or'text'in r)];guard_task_runs(self.path.parent,action='replace state');backup_stamps=[parsed for row in records for key in('at','acknowledged_at','ack_edited_at','seen_at','updated_at','published_at','agent_seen_at','confirmed_at','stamp')if(parsed:=import_stamp(row.get(key)))is not None];backup_stamp=max(backup_stamps,default=None)
		if backup_stamp is not None:
			with closing(self.connect())as live_db:live_notes=[message_row(row)for row in live_db.execute('SELECT * FROM notes ORDER BY at, seq')];live_answers=[message_row(row)for row in live_db.execute('SELECT * FROM submissions ORDER BY seq')]
		else:live_notes,live_answers=[],[]
		preserved=[row for row in(*live_notes,*live_answers)if row.get('type')!='state-backup'and(created:=import_stamp(row.get('at')))is not None and created>backup_stamp];preserved_keys={('answer'if'report_id'in row else'note',row.get('id'))for row in preserved};messages=[row for row in messages if('answer'if'report_id'in row else'note',row.get('id'))not in preserved_keys]+preserved
		with self.transaction(autosave=False)as db:
			db.execute('BEGIN IMMEDIATE')
			if history:
				receipts={}
				for row in history:key=autosave_scope_key(row['scope']);receipts.setdefault(key,{}).update(row.get('receipts',{}));receipts[key][row['save_id']]=row['digest']
				db.execute('INSERT OR REPLACE INTO meta VALUES (?, ?)',(AUTOSAVE_HISTORY,json.dumps(history)));db.execute('INSERT OR REPLACE INTO meta VALUES (?, ?)',(AUTOSAVE_RECEIPTS,json.dumps(receipts)))
			for table in('notes','reports','submissions','tasks'):
				if table=='notes':db.execute("DELETE FROM notes WHERE type <> 'state-backup'")
				else:db.execute(f"DELETE FROM {table}")
			self.import_tasks(tasks,replace_tasks,autosave=False,shared=db)
			for record in messages:
				if'text'not in record or'title'in record:raise ValueError('Each record must be a note, task or report answer')
				options={key:record.get(key)for key in('acknowledged_at','ack_type','ack_text','ack_edited_at','seen_at','task_id','replies','ack_edited_seen_count')};args=[record['id']];writer=self.note
				if'report_id'in record:writer=self.submission;args.append(record['report_id']);options['answer_data']=record.get('answer_data')
				else:options.update({key:record[key]for key in('quiet','blocking','type')if key in record})
				writer(*args,record['text'],record.get('at'),shared=db,autosave=False,**options)
			self.import_reports(reports,shared=db)
		receipt={'notes':sum('report_id'not in r for r in messages),'answers':sum('report_id'in r for r in messages),'reports':len(reports),'tasks':len(tasks)};return receipt
	def compare_state(self,text):
		records=parse_state_import(text);core_records=[row for row in records if row.get('type')!='userscript-autosave'];live_raw,_=self.state_lines(self.state()['notes'],self.tasks()or{});live_core,live_stamp=autosave_core_and_stamp(live_raw);file_core,file_stamp=autosave_core_and_stamp(core_records);groups={}
		for name in('notes','answers','reports','tasks'):live_map={row['id']:row for row in live_core if _state_record_category(row)==name};file_map={row['id']:row for row in file_core if _state_record_category(row)==name};only_live=sorted(set(live_map)-set(file_map));only_file=sorted(set(file_map)-set(live_map));changed=sorted(record_id for record_id in set(live_map)&set(file_map)if live_map[record_id]!=file_map[record_id]);groups[name]={'live':len(live_map),'file':len(file_map),'only_live':only_live,'only_file':only_file,'changed':changed}
		if all(not row['only_live']and not row['only_file']and not row['changed']for row in groups.values()):verdict='equal'
		elif all(not groups[name]['only_file']for name in('notes','answers','tasks'))and any(row['only_live']or row['changed']for row in groups.values()):verdict='ahead'
		elif all(not row['only_live']and not row['changed']for row in groups.values())and any(row['only_file']for row in groups.values()):verdict='stale'
		else:verdict='diverged'
		return{'verdict':verdict,'live_stamp':live_stamp,'file_stamp':file_stamp,'groups':groups}
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
			metadata=record.get('submission_format')
			if metadata is not None:metadata=validate_submission_format(metadata,parse_fields(markdown)[1])
			pinned=record.get('pinned',False)
			if not isinstance(pinned,bool):raise TypeError('A saved report pin is a boolean')
			ack_seen_at=record.get('ack_seen_at')
			if ack_seen_at is not None:
				if not isinstance(ack_seen_at,str):raise ValueError('A saved acknowledgement-view stamp is text')
				when(ack_seen_at)
			source=record.get('source')
			if source is not None and not isinstance(source,str):raise ValueError('A saved report source path is text')
			prepared.append((report_id,title,markdown,published_at,seen_at,seen_at is not None,source,ack_seen_at,pinned,metadata))
		state_root=self.path.parent.resolve();created=[]
		try:
			with self.transaction(shared)as db:
				for(report_id,title,markdown,published_at,seen_at,ever_seen,source,ack_seen_at,pinned,metadata)in prepared:
					target=None
					if source:
						candidate=Path(source)
						if not candidate.is_absolute():candidate=state_root/candidate
						try:relative=candidate.relative_to(state_root)
						except ValueError:relative=None
						if relative is not None and relative.parts and'..'not in relative.parts and relative.suffix.lower()=='.md':
							current=state_root;valid=True
							for part in relative.parts[:-1]:
								current=current/part
								if current.is_symlink()or current.exists()and not current.is_dir():valid=False;break
							leaf=current/relative.parts[-1]
							if valid and not leaf.is_symlink()and not leaf.exists():target=leaf
					if target is None:
						reports_dir=state_root/'reports'
						if reports_dir.is_symlink()or reports_dir.exists()and not reports_dir.is_dir():reports_dir=state_root
						stem=f"{report_id}.md";target=reports_dir/stem;suffix=2
						while target.is_symlink()or target.exists():target=reports_dir/f"{report_id}-{suffix}.md";suffix+=1
					parent=target.parent;missing_dirs=[];probe=parent
					while probe!=state_root and not probe.exists():missing_dirs.append(probe);probe=probe.parent
					for directory in reversed(missing_dirs):directory.mkdir();created.append(directory)
					with target.open('xb')as stream:stream.write(markdown.encode('utf-8'))
					created.append(target);highest=db.execute('SELECT COALESCE(MAX(seq), 0) FROM reports').fetchone()[0];db.execute('INSERT INTO reports\n               (id, title, markdown, updated_at, published_at, seq, seen_at, ever_seen, source,\n                ack_seen_at, pinned, submission_format)\n               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)\n               ON CONFLICT(id) DO UPDATE SET title = excluded.title,\n                 markdown = excluded.markdown, updated_at = excluded.updated_at,\n                 source = COALESCE(excluded.source, reports.source),\n                 published_at = COALESCE(reports.published_at, excluded.published_at),\n                 seen_at = COALESCE(reports.seen_at, excluded.seen_at),\n                 ack_seen_at = COALESCE(reports.ack_seen_at, excluded.ack_seen_at),\n                 pinned = MAX(reports.pinned, excluded.pinned),\n                 submission_format = excluded.submission_format,\n                 ever_seen = MAX(reports.ever_seen, excluded.ever_seen)',(report_id,title,markdown,now(),published_at,highest+1,seen_at,ever_seen,str(target),ack_seen_at,int(pinned),json.dumps(metadata)if metadata is not None else None))
		except Exception:
			for path in reversed(created):
				try:
					if path.is_dir():path.rmdir()
					else:path.unlink(missing_ok=True)
				except OSError:pass
			raise
		return len(prepared)
	def import_tasks(self,records,replace=False,autosave=True,shared=None):
		if not isinstance(records,list):raise TypeError('Import a list of task objects')
		prepared=[]
		for(index,record)in enumerate(records,1):
			if not isinstance(record,dict):raise TypeError('Import a list of task objects')
			details=[str(item)for item in record.get('details')or[]if str(item).strip()];status=record.get('status');completed_at=record.get('completed_at');check_task(record.get('id'),record.get('title'),details)
			if status is not None and status not in TASK_STATUSES:raise ValueError(f"A task is either {' or '.join(TASK_STATUSES)}")
			task_type=record.get('type')
			if task_type is not None and task_type not in TASK_TYPES+TASK_LEGACY_TYPES:raise ValueError(f"A task type is one of {', '.join(TASK_TYPES+TASK_LEGACY_TYPES)}")
			if completed_at is not None:
				if not isinstance(completed_at,str):raise ValueError('A saved completion stamp is text')
				when(completed_at)
			prepared.append((record.get('id'),record.get('title'),details,status,record.get('order')or index,record.get('blocked'),record.get('report_id'),completed_at,task_type))
		if replace:guard_task_runs(self.path.parent,action='replace tasks')
		with self.transaction(shared,autosave=autosave)as db:
			if replace:
				for(task_id,title,*_)in prepared:
					stored=db.execute('SELECT 1 FROM tasks WHERE id = ?',(task_id,)).fetchone()
					if title is None and not stored:raise ValueError(f"A new task needs a title: {task_id}")
				db.execute('DELETE FROM tasks')
			written=[self.write_task(*item,shared=db,restoring=True)for item in prepared]
		return written
	def renumber(self,db):
		for status in TASK_STATUSES:
			rows=db.execute('SELECT id FROM tasks WHERE status = ? ORDER BY position, id',(status,)).fetchall()
			for(position,row)in enumerate(rows,1):db.execute('UPDATE tasks SET position = ? WHERE id = ?',(position,row[0]))
	def repository_root(self):return self.path.parent.parent
	def meta_value(self,key):
		with closing(self.connect())as db:row=db.execute('SELECT value FROM meta WHERE key = ?',(key,)).fetchone();return row[0]if row else None
	def _sync_board_poll_state(self,db,active,stamp):
		sid=board_branch(workspace_branch(self.repository_root()))
		if not sid:return
		row=db.execute('SELECT value FROM meta WHERE key = ?',(BOARD_STATE_META,)).fetchone()
		if not row or not row[0]:return
		try:raw=json.loads(str(row[0]))
		except Exception:return
		if not isinstance(raw,dict):return
		sessions=raw.get('sessions')
		if not isinstance(sessions,list):sessions=[];raw['sessions']=sessions
		clipped=clip_stamp(stamp);matched=False
		for s_row in sessions:
			if isinstance(s_row,dict)and board_branch(s_row.get('branch')or s_row.get('session_id')or'')==sid:
				s_row['polling']=bool(active);s_row['status']='polling'if active else'active'
				if active:s_row['polling_since']=clipped
				else:s_row.pop('polling_since',None)
				s_row['updated_at']=clipped;matched=True
		if not matched:
			entry={'branch':sid,'repo':self.repository_root().name,'status':'polling'if active else'active','polling':bool(active),'updated_at':clipped}
			if active:entry['polling_since']=clipped
			sessions.insert(0,entry)
		db.execute('INSERT OR REPLACE INTO meta VALUES (?, ?)',(BOARD_STATE_META,json.dumps(raw,ensure_ascii=False)))
	def stamp_polling(self):
		with closing(self.connect())as db,db:db.execute('INSERT OR REPLACE INTO meta VALUES (?, ?)',(POLLING_META,now()))
	def start_poll(self):
		with closing(self.connect())as db,db:stamp=now();db.execute('INSERT OR REPLACE INTO meta VALUES (?, ?)',(POLLING_META,stamp));db.execute('INSERT OR REPLACE INTO meta VALUES (?, ?)',(POLL_SINCE_META,stamp));self._sync_board_poll_state(db,True,stamp)
	def clear_polling(self):
		with closing(self.connect())as db,db:stamp=now();db.execute('DELETE FROM meta WHERE key IN (?, ?)',(POLLING_META,POLL_SINCE_META));self._sync_board_poll_state(db,False,stamp)
	def polling(self):age=seconds_since(self.meta_value(POLLING_META));return age is not None and 0<=age<POLLING_FRESH_SECONDS
	def request_interrupt(self,tab,report_id=None,armed=True,user_flagged=True,reason=None,clause_file=None,note=None):
		if not armed:self.clear_meta(AGENT_INTERRUPT_FLAG_META);self.take_interrupt();return{'agent_interrupt_pending':False}
		if not isinstance(tab,str)or tab not in{'notes','reports','tasks','downloads'}:raise ValueError('tab must name a preview tab')
		if reason is not None and reason not in VIOLATION_TYPES[tab]:raise ValueError('reason must name a violation type for this tab')
		if clause_file not in(None,'')and clause_file not in read_set_files(self.repository_root(),worker=self.board_state().get('board_role')=='worker'):raise ValueError('clause_file must name a path in the bible')
		if note is not None and not isinstance(note,str):raise ValueError('note must be text')
		cited=' '.join((note or'').split());evidence=[]
		if reason is not None:
			heading=f"User flagged violation: {reason}."
			if cited:heading+=f" Cited: {cited}"
			if tab=='reports'and report_id is not None:evidence.append(f"Report: {report_id}")
			elif tab=='tasks':
				task=current_task((self.tasks()or{}).get('upcoming',[]))
				if task and reason!='empty-task-list':evidence.append(f"Current task: {task['id']}. Is this what you are working on?")
				else:evidence.append('The task list is empty. Maybe a task should have spawned.')
		else:
			heading='The user flagged a violation.'if user_flagged else'VIOLATION'
			if tab=='reports'and report_id is not None:
				try:self.report(report_id)
				except FileNotFoundError:pass
				else:heading=f"The user flagged a violation: report {report_id} formatting is wrong."if user_flagged else f"VIOLATION: report {report_id}"
			elif tab=='tasks':
				task=current_task((self.tasks()or{}).get('upcoming',[]))
				if task:heading=f"The user flagged a violation: task {task['id']}."if user_flagged else f"VIOLATION: task {task['id']}"
				else:evidence.append('The task list is empty. Maybe a task should have spawned.')
		notice='\n'.join([heading,*evidence,'Fix the cause, then ack with only file:ln: clause.']);return self.flag_violation_note(notice,reason,clause_file,cited)
	def flag_violation_note(self,notice,type,clause_file,cited):record=self.queue_violation_note(notice,type or'self-raised');self.set_meta(AGENT_INTERRUPT_FLAG_META,json.dumps({'type':type or'','clause_file':clause_file or'','note':cited,'id':record['id']}));return{'agent_interrupt_pending':True}
	def queue_violation_note(self,text,type):
		pending=self.pending_violation_note()
		if pending is not None:return pending
		with closing(self.connect())as db:
			for _ in range(MINT_ATTEMPTS):
				note_id=new_id()
				if not db.execute('SELECT 1 FROM notes WHERE id = ?',(note_id,)).fetchone():break
			else:raise RuntimeError('The state holds no free six-character ID for a violation note')
		return self.note(note_id,text,quiet=True,blocking=True,type=type)
	def take_interrupt(self):
		with closing(self.connect())as db,db:
			db.execute('BEGIN IMMEDIATE');row=db.execute('SELECT value FROM meta WHERE key = ?',(AGENT_INTERRUPT_META,)).fetchone();flag=db.execute('SELECT value FROM meta WHERE key = ?',(AGENT_INTERRUPT_FLAG_META,)).fetchone();db.execute('DELETE FROM meta WHERE key IN (?, ?)',(AGENT_INTERRUPT_META,AGENT_INTERRUPT_FLAG_META))
			try:note_id=json.loads(flag['value']).get('id','')if flag else''
			except ValueError:note_id=''
			if note_id:db.execute('UPDATE notes SET acknowledged_at = ?, ack_type = ?, ack_text = ? WHERE id = ? AND acknowledged_at IS NULL',(now(),'reply','Disarmed on the page.',note_id))
			return row['value']if row else None
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
	def clear_meta(self,key):
		with closing(self.connect())as db,db:db.execute('DELETE FROM meta WHERE key = ?',(key,))
	def record_tool(self,name):
		if name not in TOOL_PROBE_NAMES:raise ValueError(f"unknown tool probe: {name}")
		self.touch_agent();self.set_meta(TOOL_NAME_META,name);self.set_meta(TOOL_AT_META,now())
	def touch_agent(self):self.set_meta(AGENT_SEEN_META,now());self.clear_turn_ended()
	def mark_turn_ended(self):self.set_meta(TURN_ENDED_META,now())
	def clear_turn_ended(self):
		with closing(self.connect())as db,db:db.execute('DELETE FROM meta WHERE key = ?',(TURN_ENDED_META,))
	def set_agent_key(self,key,host=None):self.set_meta(AGENT_KEY_META,json.dumps({'key':key,'host':host,'at':now()},ensure_ascii=False))
	def set_display_timezone(self,tz):
		if not isinstance(tz,str)or not tz.strip()or not zone_exists(tz.strip()):raise ValueError(f"Unknown zone {tz}")
		name=tz.strip();self.set_meta(TIMEZONE_META,name);(self.path.parent/'display_tz').write_text(f"{name}\n",encoding='utf-8');global DISPLAY_ZONE;DISPLAY_ZONE=resolve_display_zone(self);return name
	def notice_expired_key(self):
		record=self.agent_key()
		if not record:return None
		stamp=import_stamp(record['at'])
		if stamp is None:return None
		if(datetime.now(UTC)-stamp).total_seconds()<AGENT_KEY_EXPIRY_SECONDS:return None
		if self.meta_value(AGENT_KEY_EXPIRY_META)==record['at']:return None
		self.set_meta(AGENT_KEY_EXPIRY_META,record['at']);return self.note(self.free_id(),f"Proxy key {record['key'][:5]} expired. Run arena-preview key for new key.",quiet=True)
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
	def set_userscript_version(self,version):
		if not isinstance(version,str):return None
		cleaned=version.strip()
		if not USERSCRIPT_VERSION_RE.fullmatch(cleaned):return None
		if self.meta_value(USERSCRIPT_VERSION_META)!=cleaned:self.set_meta(USERSCRIPT_VERSION_META,cleaned)
		return cleaned
	def userscript_version(self):cleaned=(self.meta_value(USERSCRIPT_VERSION_META)or'').strip();return cleaned if USERSCRIPT_VERSION_RE.fullmatch(cleaned)else None
	def set_egress_version(self,version):
		if not isinstance(version,str):return None
		cleaned=version.strip()
		if not USERSCRIPT_VERSION_RE.fullmatch(cleaned):return None
		if self.meta_value(EGRESS_VERSION_META)!=cleaned:self.set_meta(EGRESS_VERSION_META,cleaned)
		return cleaned
	def egress_version(self):
		cleaned=(self.meta_value(EGRESS_VERSION_META)or'').strip()
		if USERSCRIPT_VERSION_RE.fullmatch(cleaned):return cleaned
		b_ver=str(self.board_state().get('egress_version')or'').strip();return b_ver if USERSCRIPT_VERSION_RE.fullmatch(b_ver)else None
	def stale_report_rows(self,db):
		linked={};tasks=[dict(row)for row in db.execute('SELECT id, status, report_id FROM tasks')]
		for task in link_task_answers(db,tasks):
			reports={task['report_id']}|{answer['report_id']for answer in task.get('answers',[])}
			for report_id in reports-{None}:linked.setdefault(report_id,[]).append(task['status'])
		stale=[];current=import_stamp(now());rows=db.execute('SELECT id, updated_at, viewed_at, last_viewed_at FROM reports WHERE published_at IS NOT NULL AND pinned = 0 ORDER BY seq, id')
		for row in rows:
			statuses=linked.get(row['id'])
			if statuses:
				if all(status=='finished'for status in statuses):stale.append({'id':row['id'],'reason':'all linked tasks finished'})
				continue
			stamps=[import_stamp(row[key])for key in('updated_at','viewed_at','last_viewed_at')];latest=max((stamp for stamp in stamps if stamp is not None),default=None)
			if latest is not None and current-latest>=timedelta(seconds=REPORT_IDLE_SECONDS):stale.append({'id':row['id'],'reason':'unlinked and idle for 30 minutes'})
		return stale
	def pin_report(self,report_id,pinned):
		identifier(report_id)
		with self.transaction()as db:
			changed=db.execute('UPDATE reports SET pinned = ? WHERE id = ?',(int(pinned),report_id))
			if changed.rowcount==0:raise FileNotFoundError('Report not found')
	def stale_report_ids(self,db):return[record['id']for record in self.stale_report_rows(db)]
	def stale_reports(self):
		with closing(self.connect())as db:return self.stale_report_rows(db)
	def report_state(self,report_id=None):
		with closing(self.connect())as db:
			rows=db.execute('SELECT id, title, published_at, updated_at, viewed_at, pinned FROM reports'+(' WHERE id = ?'if report_id else'')+' ORDER BY updated_at DESC',(report_id,)if report_id else()).fetchall();stale={row['id']for row in self.stale_report_rows(db)};out=[]
			for row in rows:answers=db.execute('SELECT id, acknowledged_at, seen_at FROM submissions WHERE report_id = ? ORDER BY seq',(row['id'],)).fetchall();out.append({'id':row['id'],'title':row['title'],'published_at':row['published_at'],'updated_at':row['updated_at'],'viewed_at':row['viewed_at'],'stale':row['id']in stale,'pinned':bool(row['pinned']),'answers':[{'id':answer['id'],'acknowledged':answer['acknowledged_at']is not None,'seen':answer['seen_at']is not None}for answer in answers]})
			return out
	def reminder(self,advance=False,routine=True):
		order_notice=False
		with closing(self.connect())as db,db:
			if advance and meta_number(db,TASK_ORDER_PENDING_META):order_notice=True;db.execute('DELETE FROM meta WHERE key = ?',(TASK_ORDER_PENDING_META,))
			uploads=db.execute('SELECT count(*) FROM notes JOIN uploads USING (id) WHERE acknowledged_at IS NULL AND blocking = 1').fetchone()[0];notes=db.execute('SELECT count(*) FROM notes WHERE acknowledged_at IS NULL AND blocking = 1').fetchone()[0]-uploads;reports=db.execute(f"SELECT count(*) FROM submissions WHERE {PENDING_SUBMISSIONS}").fetchone()[0];board_mentions=len(pending_board_entries_for_db(db));remaining=db.execute("SELECT count(*) FROM tasks WHERE status <> 'finished'").fetchone()[0];published=db.execute('SELECT count(*) FROM reports WHERE published_at IS NOT NULL').fetchone()[0];stale=self.stale_report_ids(db)if published>=STALE_REPORT_THRESHOLD else[];cursor=meta_number(db,REMINDER_CURSOR);polls=0 if advance and not notes+reports+uploads+board_mentions else meta_number(db,POLLS_SINCE_MESSAGE)+(1 if advance else 0);blocked=bool(gate_pending_count(db))and polls>=GATE_THRESHOLD
			if routine:db.execute('INSERT OR REPLACE INTO meta VALUES (?, ?)',(REMINDER_CURSOR,str(cursor+1)))
			if advance:db.execute('INSERT OR REPLACE INTO meta VALUES (?, ?)',(POLLS_SINCE_MESSAGE,str(polls)))
		counts=[f"{count} {type}{'s'if count!=1 else''}"for(count,type)in((notes,'note'),(reports,'answer'),(uploads,'upload'),(board_mentions,'board post'))if count];unacked=[f"unacked {', '.join(counts)}"]if counts else[]
		if blocked:head=['blocked']
		elif polls and counts:
			if polls>GATE_THRESHOLD//2:head=[f"bash blocked in {GATE_THRESHOLD-polls}"]
			else:head=[f"calls {polls}"]
		else:head=[]
		is_worker=self.board_state().get('board_role')=='worker';repo_root=self.repository_root()if routine and not is_worker else None;unpushed=unpushed_commit_count(repo_root)if repo_root else 0;private=is_private_repo(repo_root,store=self)if unpushed else False;tail=reminder_tail(cursor,remaining,len(stale),unpushed,private=private)if routine else'';parts=[' '.join([*head,*unacked]).strip()]
		if order_notice:parts.append(TASK_ORDER_NOTICE)
		unpushed_notice=unpushed_reminder_text(unpushed,private=private)if unpushed else''
		if unpushed_notice and unpushed_notice!=tail:parts.append(unpushed_notice)
		parts.append(tail);return' | '.join(parts).strip(' |')
	def calls_since_message(self):
		with closing(self.connect())as db:return meta_number(db,POLLS_SINCE_MESSAGE)
	def gate(self,threshold=GATE_THRESHOLD,pending_only=False):
		with closing(self.connect())as db,db:pending=gate_pending_count(db);polls=meta_number(db,POLLS_SINCE_MESSAGE)
		if pending_only:return not pending
		return not(pending and polls>=threshold)
	def read(self,include_quiet=True):
		quiet_filter=' AND (quiet = 0 OR blocking = 1 OR seen_at IS NULL)'if include_quiet else" AND (quiet = 0 OR (type IN ('state-backup', 'survey', 'worker-idle', 'worker-disconnected') AND blocking = 1))"
		with self.transaction(autosave=False)as db:
			pending=[message_row(row)|{'type':'note'}for row in db.execute(f"SELECT * FROM notes WHERE acknowledged_at IS NULL{quiet_filter} ORDER BY at, seq")];pending+=[message_row(row)|{'type':'report'}for row in db.execute(f"SELECT submissions.*,\n                     (SELECT title FROM reports WHERE reports.id = submissions.report_id) AS title\n              FROM submissions WHERE {PENDING_SUBMISSIONS}\n              ORDER BY submissions.at, submissions.seq")];repo_root=self.repository_root()
			for b_entry in pending_board_entries_for_db(db,branch=board_branch(workspace_branch(repo_root))):pending.append({'id':str(b_entry.get('id')or''),'type':'board','branch':str(b_entry.get('branch')or''),'board_id':str(b_entry.get('board_id')or b_entry.get('branch')or''),'text':str(b_entry.get('text')or''),'at':str(b_entry.get('at')or now()),'seen_at':b_entry.get('seen_at'),'acknowledged_at':b_entry.get('acknowledged_at'),'ack_edited_at':b_entry.get('ack_edited_at'),'file':b_entry.get('file')if isinstance(b_entry.get('file'),dict)else None,'patch_hint':board_patch_apply_hint(b_entry,state_dir=self.path.parent,repo_root=repo_root)})
			notes={};board_patch_hints={}
			for table in('notes','submissions'):
				for row in db.execute(f"SELECT * FROM {table} ORDER BY at, seq"):record=message_row(row);notes[record['id']]=record
			for row in db.execute('SELECT id, source FROM reports'):notes[row['id']]=dict(row)|{'type':'report','text':''}
			for row in db.execute(f"SELECT {TASK_COLUMNS} FROM tasks"):task=task_row(row);notes[task['id']]={'id':task['id'],'text':'\n'.join([task['title'],*task['details']])}
			b_row=db.execute('SELECT value FROM meta WHERE key = ?',(BOARD_STATE_META,)).fetchone()
			if b_row and b_row[0]:
				try:b_raw=json.loads(str(b_row[0]))
				except Exception:b_raw=None
				if isinstance(b_raw,dict):
					for b_entry in b_raw.get('entries')or[]:
						if not isinstance(b_entry,dict):continue
						b_eid=str(b_entry.get('id')or'');b_nid=str(b_entry.get('note_id')or'');hint=board_patch_apply_hint(b_entry,state_dir=self.path.parent,repo_root=repo_root)
						if hint:
							if b_eid:board_patch_hints[b_eid]=hint
							if b_nid:board_patch_hints[b_nid]=hint
						if b_eid and b_eid not in notes:payload_obj=b_entry.get('payload');b_text=str(b_entry.get('text')or(payload_obj.get('text')if isinstance(payload_obj,dict)else'')or'');notes[b_eid]={'id':b_eid,'text':b_text,'at':b_entry.get('at')}
			attachments={}
			for row in db.execute('SELECT * FROM uploads ORDER BY seq'):record=upload_row(row,self.path.parent);attachments.setdefault(record['note_id'],[]).append(record)
			for record in notes.values():add_note_attachments(record,attachments.get(record['id'],[]))
			for item in pending:
				if item['type']=='note':
					add_note_attachments(item,attachments.get(item['id'],[]))
					if item['id']in board_patch_hints:item['patch_hint']=board_patch_hints[item['id']]
					thread=reply_thread(item,notes)
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
				else:
					b_row=db.execute('SELECT value FROM meta WHERE key = ?',(BOARD_STATE_META,)).fetchone();b_raw=None
					if b_row and b_row[0]:
						try:b_raw=json.loads(str(b_row[0]))
						except Exception:b_raw=None
					matched_board=False
					if isinstance(b_raw,dict)and isinstance(b_raw.get('entries'),list):
						for entry in b_raw['entries']:
							if isinstance(entry,dict)and(entry.get('id')==record_id or str(entry.get('id')or'')[:6]==record_id):entry['seen_at']=entry.get('seen_at')or board_stamp(stamp);matched_board=True;break
					if matched_board:db.execute('INSERT OR REPLACE INTO meta VALUES (?, ?)',(BOARD_STATE_META,json.dumps(b_raw,ensure_ascii=False)))
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
			b_row=db.execute('SELECT value FROM meta WHERE key = ?',(BOARD_STATE_META,)).fetchone()
			if b_row and b_row['value']:
				try:b_raw=json.loads(b_row['value'])
				except Exception:b_raw=None
				if isinstance(b_raw,dict)and isinstance(b_raw.get('entries'),list):
					for entry in b_raw['entries']:
						if isinstance(entry,dict)and str(entry.get('id')or'')==record_id:
							b_replies=entry.get('replies')if isinstance(entry.get('replies'),list)else[]
							if count>len(b_replies):raise ValueError('The viewed-reply count exceeds the current replies')
							seen_count=max(int(entry.get('ack_edited_seen_count')or 0),count);entry['ack_edited_seen_count']=seen_count;rep_ops=list(b_raw['report_ops'])if isinstance(b_raw.get('report_ops'),list)else[];rep_ops.append({'id':f"erseen-{record_id}-{seen_count}",'target':'*','op':'entry-ack','entry_id':record_id,'ack_edited_seen_count':seen_count,'at':board_stamp()});b_raw['report_ops']=rep_ops;db.execute('INSERT OR REPLACE INTO meta VALUES (?, ?)',(BOARD_STATE_META,json.dumps(b_raw,ensure_ascii=False)));return{'id':record_id,'ack_edited_seen_count':seen_count}
		raise FileNotFoundError('Message not found')
	def unlink_submission(self,record_id):
		identifier(record_id)
		with self.transaction()as db:
			cursor=db.execute('UPDATE submissions SET task_id = ? WHERE id = ?',(UNLINKED_TASK_ID,record_id))
			if not cursor.rowcount:raise ValueError(f"No submission is stored under {record_id}")
			return message_row(db.execute('SELECT * FROM submissions WHERE id = ?',(record_id,)).fetchone())
	def mark_task(self,record_id,task_id,shared=None):
		identifier(record_id);identifier(task_id)
		with self.transaction(shared)as db:
			for table in('notes','submissions'):
				cursor=db.execute(f"UPDATE {table} SET task_id = ? WHERE id = ?",(task_id,record_id))
				if cursor.rowcount:
					if table=='submissions':db.execute('UPDATE tasks SET blocked = 0, updated_at = ? WHERE id = ? AND blocked = 1',(now(),task_id))
					if table=='notes':
						uploads=db.execute('SELECT * FROM uploads WHERE note_id = ? ORDER BY seq',(record_id,)).fetchall()
						if uploads:
							stored=db.execute('SELECT details FROM tasks WHERE id = ?',(task_id,)).fetchone();details=json.loads(stored['details'])if stored else[];paths=[f"Attachment: {upload_row(row,self.path.parent)['path']}"for row in uploads];added=[path for path in paths if path not in details]
							if added:return self.write_task(task_id,details=added,append_details=True,shared=db)
					return
		raise ValueError(f"Unknown note: {record_id}; no task marker written")
	def pending_ack(self):
		with self.connect()as db:
			rows=[{'id':row['id'],'at':str(row['at']or'')}for row in db.execute(f"SELECT id, at FROM notes WHERE acknowledged_at IS NULL AND blocking = 1 UNION ALL SELECT id, at FROM submissions WHERE {PENDING_SUBMISSIONS} ORDER BY at").fetchall()]
			for b_entry in pending_board_entries_for_db(db,branch=board_branch(workspace_branch(self.repository_root()))):rows.append({'id':str(b_entry.get('id')or''),'at':str(b_entry.get('at')or'')})
		rows.sort(key=lambda r:r['at'])
		if not rows:raise ValueError('No pending note or answer to acknowledge')
		if len(rows)>1:listed=', '.join(row['id']for row in rows);raise ValueError(f"{len(rows)} items are pending: {listed}. Name the ID to answer one")
		return rows[0]['id']
	def acknowledge(self,ids,text,shared=None):
		text=note_text(text);stamp=now();patch_hints=[];repo_root=self.repository_root()
		with self.transaction(shared)as db:
			for record_id in ids:identifier(record_id)
			for record_id in ids:
				row=db.execute(f"SELECT 1 FROM notes WHERE id = ? AND {VIOLATION_NOTES}",(record_id,)).fetchone()
				if row is not None:
					error=clause_ack_error(text,self.repository_root())
					if error:raise ValueError(error)
			b_row=db.execute('SELECT value FROM meta WHERE key = ?',(BOARD_STATE_META,)).fetchone();b_raw=None
			if b_row and b_row[0]:
				try:b_raw=json.loads(str(b_row[0]))
				except Exception:b_raw=None
			board_dirty=False
			for record_id in ids:
				matched_db=False
				for table in('notes','submissions'):
					cols='acknowledged_at, replies, quiet, blocking, type'if table=='notes'else'acknowledged_at, replies';row=db.execute(f"SELECT {cols} FROM {table} WHERE id = ?",(record_id,)).fetchone()
					if row is None:continue
					if row['acknowledged_at']is None:
						db.execute(f"UPDATE {table} SET acknowledged_at = ?, ack_type = ?, ack_text = ?, seen_at = COALESCE(seen_at, ?) WHERE id = ?",(stamp,'reply',text,stamp,record_id))
						if table=='notes'and row['quiet']and row['blocking']and row['type']=='':db.execute("INSERT OR REPLACE INTO meta VALUES ('survey_checked_at', ?)",(stamp,))
					else:more=replies_list(row['replies'])+[{'type':'reply','text':text,'at':stamp}];db.execute(f"UPDATE {table} SET replies = ?, ack_edited_at = ? WHERE id = ?",(json.dumps(more,ensure_ascii=False),stamp,record_id))
					if table=='submissions':db.execute('UPDATE reports SET agent_seen_at = ? WHERE id = (SELECT report_id FROM submissions WHERE id = ?)',(stamp,record_id))
					matched_db=True;break
				if isinstance(b_raw,dict)and isinstance(b_raw.get('entries'),list):
					for entry in b_raw['entries']:
						if not isinstance(entry,dict):continue
						eid=str(entry.get('id')or'');nid=str(entry.get('note_id')or'');alt_id=f"p{eid[2:7]}"if re.match('^b-[0-9a-f]{5,}',eid,re.IGNORECASE)else''
						if eid==record_id or eid[:6]==record_id or alt_id and(alt_id==record_id or alt_id[:6]==record_id)or nid and(nid==record_id or nid[:6]==record_id):
							local_sid=board_branch(b_raw.get('local_branch')or b_raw.get('local_session_id')or workspace_branch(self.repository_root()));norm_b=normalize_board_state(b_raw,repo=self.repository_root().name,branch=local_sid);ack_sender_id=str(local_sid or norm_b.get('branch')or'');norm_ack_msg=normalize_board_mentions(text,norm_b.get('sessions')or[],allowed=norm_b.get('allowed_sessions')or[],main_sid=norm_b.get('main_session_id')or'',from_sid=ack_sender_id,to_sid=str(entry.get('branch')or entry.get('session_id')or''))
							if not entry.get('acknowledged_at'):entry['acknowledged_at']=board_stamp(stamp);entry['ack_type']='reply';entry['ack_text']=norm_ack_msg;entry['ack_sender']=ack_sender_id;entry['seen_at']=entry.get('seen_at')or board_stamp(stamp)
							else:replies=list(entry['replies'])if isinstance(entry.get('replies'),list)else[];replies.append({'type':'reply','text':norm_ack_msg,'at':board_stamp(stamp),'sender':ack_sender_id});entry['replies']=replies;entry['ack_edited_at']=board_stamp(stamp)
							rep_ops=list(b_raw['report_ops'])if isinstance(b_raw.get('report_ops'),list)else[];rep_ops.append({'id':f"eack-{eid}-{uuid.uuid4().hex[:6]}",'target':'*','op':'entry-ack','entry_id':eid,'seen_at':entry.get('seen_at'),'acknowledged_at':entry.get('acknowledged_at'),'ack_type':entry.get('ack_type'),'ack_text':entry.get('ack_text'),'ack_sender':entry.get('ack_sender'),'ack_edited_at':entry.get('ack_edited_at'),'replies':entry.get('replies'),'at':board_stamp(stamp)});b_raw['report_ops']=rep_ops;sender_sid=str(entry.get('branch')or entry.get('session_id')or'')
							if sender_sid and sender_sid!=local_sid:wakes=dict(b_raw['wakes'])if isinstance(b_raw.get('wakes'),dict)else{};wakes[sender_sid]=f"{eid}-ack";b_raw['wakes']=wakes
							hint=board_patch_apply_hint(entry,state_dir=self.path.parent,repo_root=repo_root)
							if hint and hint not in patch_hints:patch_hints.append(hint)
							board_dirty=True;matched_db=True;break
				if not matched_db:raise ValueError(f"Unknown note: {record_id}; no receipts written")
			if board_dirty and isinstance(b_raw,dict):db.execute('INSERT OR REPLACE INTO meta VALUES (?, ?)',(BOARD_STATE_META,json.dumps(b_raw,ensure_ascii=False)))
			if not db.execute(f"SELECT 1 FROM notes WHERE {VIOLATION_NOTES} AND acknowledged_at IS NULL").fetchone():db.execute('DELETE FROM meta WHERE key = ?',(AGENT_INTERRUPT_FLAG_META,))
			db.execute("INSERT OR REPLACE INTO meta VALUES ('last_check', ?)",(stamp,))
			if not gate_pending_count(db):db.execute("INSERT OR REPLACE INTO meta VALUES (?, '0')",(POLLS_SINCE_MESSAGE,))
		return patch_hints
	def guard_report_change(self,db,report_id,report,hold_view=True,hold_answer=True):
		unread=db.execute(f"SELECT 1 FROM submissions WHERE report_id = ? AND {PENDING_SUBMISSIONS} LIMIT 1",(report_id,)).fetchone()
		if unread is not None:raise ReportHeld('The report has an unacknowledged answer.')
		acknowledged=db.execute('SELECT MAX(acknowledged_at) FROM submissions WHERE report_id = ?',(report_id,)).fetchone()[0]
		if acknowledged and not at_or_after(report['ack_seen_at'],acknowledged):raise ReportHeld('The report waits for the owner: its answer acknowledgement is unseen.')
		answered=db.execute('SELECT 1 FROM submissions WHERE report_id = ? LIMIT 1',(report_id,)).fetchone()
		if hold_view:
			age=seconds_since(report['viewed_at'])
			if answered is not None and age is not None and 0<=age<REPORT_CHANGE_VIEW_SECONDS:remaining=max(1,int(REPORT_CHANGE_VIEW_SECONDS-age));raise ReportHeld(f"The owner is reading this report: retry after {remaining} s without a view.")
		if hold_answer and answered is not None:raise ReportHeld('The report has a submitted answer; publish the update under a new ID.')
	def publish(self,report_id,title,source,submission_format=None,*,allow_unparsed_markers=True):
		identifier(report_id)
		if not isinstance(title,str)or not title.strip()or len(title)>200:raise ValueError('Report title must contain 1–200 characters')
		source=Path(source)
		if source.suffix.lower()!='.md':raise ValueError('Publish a UTF-8 .md source file')
		with source.open('rb')as stream:data=stream.read(MAX_REPORT+1)
		if len(data)>MAX_REPORT:raise ValueError('Report exceeds the 2 MB limit; split it into reports')
		raw_text=normalize_report_mark_tags(data.decode('utf-8'));text=embed_report_images(raw_text,source.resolve().parent)
		if len(text.encode('utf-8'))>MAX_REPORT:raise ValueError('Report exceeds the 2 MB limit; split it into reports')
		fields=parse_fields(text)[1];metadata=validate_submission_format(submission_format,fields)
		if not allow_unparsed_markers:
			warnings=report_marker_warnings(text)
			if warnings:lines=[f"{source}:{warning['line']}: Error: possible unparsed {warning['marker']} marker. Keep spaces outside the markers, or pass -u / --allow-unparsed-markers if literal marker text is intentional."for warning in warnings];raise ValueError('\n'.join(lines))
		check_report_nemoizer(source,raw_text,self.repository_root())
		with self.transaction()as db:
			report=db.execute('SELECT ack_seen_at, viewed_at FROM reports WHERE id = ?',(report_id,)).fetchone()
			if report is not None:self.guard_report_change(db,report_id,report)
			else:
				answered=db.execute('SELECT 1 FROM submissions WHERE report_id = ? LIMIT 1',(report_id,)).fetchone()
				if answered is not None:raise ValueError(f"Report {report_id} has submitted answers; publish the update under a new ID")
			self.refuse_shared_id(db,'reports','tasks',report_id);highest=db.execute('SELECT COALESCE(MAX(seq), 0) FROM reports').fetchone()[0];db.execute('INSERT INTO reports (id, title, markdown, updated_at, published_at, seq, source, submission_format)\n           VALUES (?, ?, ?, ?, ?, ?, ?, ?)\n           ON CONFLICT(id) DO UPDATE SET title = excluded.title,\n             markdown = excluded.markdown, updated_at = excluded.updated_at,\n             source = excluded.source, submission_format = excluded.submission_format,\n             seq = COALESCE(reports.seq, excluded.seq), seen_at = NULL',(report_id,title,text,now(),now(),highest+1,str(source.resolve()),json.dumps(metadata)if metadata is not None else None))
		return len(fields)
	def unpublish(self,report_id,dismissed_by_owner=False,*,stale_only=False,_from_board=False):
		identifier(report_id);mirrored=None
		with self.transaction()as db:
			if stale_only:db.execute('BEGIN IMMEDIATE')
			row=db.execute('SELECT title, ack_seen_at, viewed_at FROM reports WHERE id = ?',(report_id,)).fetchone()
			if row is None:
				if stale_only:return False
				if _from_board:raise FileNotFoundError('Report not found')
				mirrored=self._mirrored_worker_reports().get(report_id)
				if mirrored is None:raise FileNotFoundError('Report not found')
				db.execute('INSERT OR REPLACE INTO meta VALUES (?, ?)',(REMOVED_META,now()))
				if dismissed_by_owner:db.execute('INSERT INTO notes (id, text, at, quiet, blocking) VALUES (?, ?, ?, 1, 0)',(new_id(),f"Owner dismissed report {report_id}.",now()))
			else:
				if stale_only and report_id not in self.stale_report_ids(db):return False
				if not dismissed_by_owner and not _from_board:self.guard_report_change(db,report_id,row,hold_view=False,hold_answer=False)
				db.execute('DELETE FROM reports WHERE id = ?',(report_id,));self.release_report_tasks(db,report_id);db.execute('INSERT OR REPLACE INTO meta VALUES (?, ?)',(REMOVED_META,now()))
				if dismissed_by_owner:db.execute('INSERT INTO notes (id, text, at, quiet, blocking) VALUES (?, ?, ?, 1, 0)',(new_id(),f"Owner dismissed report {report_id}.",now()))
		if mirrored is not None:self.board_action({'action':'worker-report','target':mirrored['worker_branch'],'op':'unpublish','report_id':mirrored['worker_report_id'],'from_owner':bool(dismissed_by_owner)});return True
		if not _from_board and not stale_only and dismissed_by_owner:
			try:
				b_state=self.board_state();self_sid=str(b_state.get('branch')or'');allowed_set=set(b_state.get('allowed_sessions')or[])
				if self_sid and self_sid in allowed_set:
					if b_state.get('board_role')=='worker':self.board_action({'action':'worker-report','target':self_sid,'op':'unpublish','report_id':report_id,'from_owner':True})
					elif b_state.get('board_role')=='main':
						fwd=self._forwarded_worker_reports().get(report_id)
						if fwd and fwd.get('worker_branch'):self.board_action({'action':'worker-report','target':fwd['worker_branch'],'op':'unpublish','report_id':fwd['worker_report_id'],'from_owner':True})
						else:
							for(w_sid,rep_list)in(b_state.get('worker_reports')or{}).items():
								if not w_sid or w_sid==self_sid or w_sid not in allowed_set or not isinstance(rep_list,list):continue
								if any(isinstance(r,dict)and str(r.get('id')or'')==report_id for r in rep_list):self.board_action({'action':'worker-report','target':w_sid,'op':'unpublish','report_id':report_id,'from_owner':True})
			except Exception:pass
		if stale_only:return True
	def clear_state(self):
		guard_task_runs(self.path.parent,action='clear state');self.rotate_backup();counts={}
		with self.transaction()as db:
			for table in('notes','reports','submissions','tasks','uploads','fetch_jobs'):counts[table]=db.execute(f"SELECT count(*) FROM {table}").fetchone()[0];db.execute(f"DELETE FROM {table}")
			kept=db.execute('SELECT key, value FROM meta WHERE key IN (?, ?)',(AGENT_KEY_META,'port')).fetchall();db.execute('DELETE FROM meta')
			for(key,value)in kept:db.execute('INSERT INTO meta VALUES (?, ?)',(key,value))
			db.execute("INSERT OR REPLACE INTO meta VALUES (?, '1')",(CLEARED_META,));db.execute('INSERT OR REPLACE INTO meta VALUES (?, ?)',(CLEARED_AT_META,board_stamp()));db.execute('INSERT OR REPLACE INTO meta VALUES (?, ?)',(FORCE_SAVE_META,str(uuid.uuid4())))
		return counts
	def state_usage(self):return state_dir_usage(self.path.parent)
	def clean_state(self,dry_run=False):
		state_root=self.path.parent.resolve();keep_files={(state_root/'state.sqlite3').resolve(),(state_root/'state.sqlite3-wal').resolve(),(state_root/'state.sqlite3-shm').resolve(),(state_root/'display_tz').resolve(),(state_root/'pr-body.md').resolve()};repo_root=self.repository_root().resolve()
		with closing(self.connect())as db:report_rows=db.execute('SELECT id, markdown FROM reports').fetchall();upload_rows=db.execute('SELECT u.file, u.name, u.note_id, n.acknowledged_at FROM uploads u LEFT JOIN notes n ON n.id = u.note_id').fetchall();download_rows=db.execute('SELECT file FROM fetch_jobs WHERE file IS NOT NULL').fetchall()
		for row in report_rows:
			keep_files.add((state_root/f"{row['id']}.md").resolve())
			for match in re.finditer('!\\[[^\\]]*\\]\\(([^)\\s]+)',row['markdown']or''):
				raw_url=(match.group(1)or'').strip()
				if not raw_url or raw_url.startswith(('http://','https://','data:','/')):continue
				for base in(state_root,repo_root):
					candidate=(base/raw_url).resolve()
					if candidate.exists():keep_files.add(candidate)
		for md_file in state_root.glob('*.md'):
			keep_files.add(md_file.resolve())
			try:md_text=md_file.read_text(encoding='utf-8')
			except OSError:continue
			for match in re.finditer('!\\[[^\\]]*\\]\\(([^)\\s]+)',md_text):
				raw_url=(match.group(1)or'').strip()
				if not raw_url or raw_url.startswith(('http://','https://','data:','/')):continue
				for base in(state_root,repo_root):
					candidate=(base/raw_url).resolve()
					if candidate.exists():keep_files.add(candidate)
		for row in upload_rows:
			if row['note_id']is not None and row['acknowledged_at']is not None and(row['name']or'').lower().endswith('.ndjson'):continue
			if row['file']:keep_files.add((state_root/'uploads'/row['file']).resolve())
		for row in download_rows:
			if row['file']:keep_files.add((state_root/'downloads'/row['file']).resolve())
		active_runs=set(live_runs(state_root));runs_root=state_root/'runs'
		if runs_root.is_dir():
			for record_file in runs_root.glob('*.json'):
				run_name=record_file.stem
				try:record=json.loads(record_file.read_text(encoding='utf-8'))
				except(OSError,ValueError):record={}
				if not isinstance(record,dict):record={}
				if run_name in active_runs or record.get('exit')is None or not record.get('noticed'):keep_files.add(record_file.resolve());keep_files.add((runs_root/f"{run_name}.log").resolve())
		worktrees_root=state_root/'worktrees'
		if worktrees_root.is_dir():
			for wt_file in worktrees_root.glob('*.json'):
				try:wt_record=json.loads(wt_file.read_text(encoding='utf-8'))
				except(OSError,ValueError):wt_record={}
				wt_path=wt_record.get('worktree')if isinstance(wt_record,dict)else None
				if wt_path and Path(wt_path).exists():keep_files.add(wt_file.resolve())
		removed_files=0;removed_bytes=0;removed_paths=[]
		for(dirpath,_,filenames)in os.walk(state_root,followlinks=False):
			rel_parts=Path(dirpath).relative_to(state_root).parts;top=rel_parts[0]if rel_parts else''
			if top=='backups':continue
			for name in sorted(filenames):
				if not top and name.endswith('-pr-body.md'):continue
				target=(Path(dirpath)/name).resolve()
				if target in keep_files:continue
				try:size=target.stat().st_size
				except OSError:continue
				rel_name=target.relative_to(state_root).as_posix()
				if not dry_run:
					try:target.unlink()
					except OSError:continue
				removed_files+=1;removed_bytes+=size;removed_paths.append(rel_name)
		return{'removed_files':removed_files,'removed_bytes':removed_bytes,'paths':removed_paths,'dry_run':bool(dry_run),'usage':state_dir_usage(state_root)}
	def mark_report_seen(self,report_id):
		identifier(report_id);mirrored=None
		with self.transaction()as db:
			row=db.execute('SELECT * FROM reports WHERE id = ?',(report_id,)).fetchone()
			if row is None:
				mirrored=self._mirrored_worker_reports().get(report_id)
				if mirrored is None:raise FileNotFoundError('Report not found')
				b_row=db.execute('SELECT value FROM meta WHERE key = ?',(BOARD_STATE_META,)).fetchone();raw_text=str(b_row[0])if b_row and b_row[0]else''
				try:stored=json.loads(raw_text)if raw_text else{}
				except Exception:stored={}
				if not isinstance(stored,dict):stored={}
				ack_wakes=dict(stored.get('ack_wakes'))if isinstance(stored.get('ack_wakes'),dict)else{};b_state=self.board_state();self_sid=str(b_state.get('branch')or'');seen_map_key=f"{self_sid}:mirrored_report_seen";seen_map=dict(ack_wakes.get(seen_map_key))if isinstance(ack_wakes.get(seen_map_key),dict)else{};seen_key=f"{mirrored['worker_branch']}:{mirrored['worker_report_id']}";existing=seen_map.get(seen_key)if isinstance(seen_map,dict)else None;stamp=existing.get('seen_at')if isinstance(existing,dict)and existing.get('updated_at')==mirrored['updated_at']and existing.get('seen_at')else clip_stamp(now());seen_map[seen_key]={**(existing if isinstance(existing,dict)else{}),'updated_at':mirrored['updated_at'],'seen_at':stamp};ack_wakes[seen_map_key]=seen_map;stored['ack_wakes']=ack_wakes;db.execute('INSERT OR REPLACE INTO meta VALUES (?, ?)',(BOARD_STATE_META,json.dumps(stored)));return{**mirrored,'seen_at':stamp,'ever_seen':1}
			db.execute('UPDATE reports SET seen_at = COALESCE(seen_at, ?), ever_seen = 1 WHERE id = ?',(now(),report_id));return dict(db.execute('SELECT * FROM reports WHERE id = ?',(report_id,)).fetchone())
	def mark_report_ack_seen(self,report_id):
		identifier(report_id)
		with self.transaction()as db:
			row=db.execute('SELECT * FROM reports WHERE id = ?',(report_id,)).fetchone()
			if row is None:
				mirrored=self._mirrored_worker_reports().get(report_id)
				if mirrored is None:raise FileNotFoundError('Report not found')
				raw_text=self.meta_value(BOARD_STATE_META)
				try:stored=json.loads(raw_text)if raw_text else{}
				except Exception:stored={}
				if not isinstance(stored,dict):stored={}
				ack_wakes=dict(stored.get('ack_wakes'))if isinstance(stored.get('ack_wakes'),dict)else{};self_sid=board_branch(workspace_branch(self.repository_root()))or'main';seen_map_key=f"{self_sid}:mirrored_report_seen";seen_map=dict(ack_wakes.get(seen_map_key))if isinstance(ack_wakes.get(seen_map_key),dict)else{};seen_key=f"{mirrored['worker_branch']}:{mirrored['worker_report_id']}";existing=seen_map.get(seen_key)if isinstance(seen_map,dict)else None;stamp=clip_stamp(now());seen_map[seen_key]={**(existing if isinstance(existing,dict)else{}),'updated_at':mirrored['updated_at'],'seen_at':existing.get('seen_at')if isinstance(existing,dict)and existing.get('seen_at')else mirrored.get('seen_at')or stamp,'ack_seen_at':stamp};ack_wakes[seen_map_key]=seen_map;stored['ack_wakes']=ack_wakes;db.execute('INSERT OR REPLACE INTO meta VALUES (?, ?)',(BOARD_STATE_META,json.dumps(stored)));return{**mirrored,'ack_seen_at':stamp}
			db.execute('UPDATE reports SET ack_seen_at = ? WHERE id = ?',(now(),report_id));return dict(db.execute('SELECT * FROM reports WHERE id = ?',(report_id,)).fetchone())
	def mark_report_viewed(self,report_id,active=True):
		identifier(report_id)
		with closing(self.connect())as db,db:
			row=db.execute('SELECT 1 FROM reports WHERE id = ?',(report_id,)).fetchone()
			if row is None:
				mirrored=self._mirrored_worker_reports().get(report_id)
				if mirrored is None:raise FileNotFoundError('Report not found')
				return{'id':report_id,'viewed_at':clip_stamp(now()if active else None)}
			stamp=now()if active else None;db.execute('UPDATE reports SET viewed_at = ?, last_viewed_at = COALESCE(?, last_viewed_at) WHERE id = ?',(stamp,stamp,report_id))
		return{'id':report_id,'viewed_at':clip_stamp(stamp)}
	def report(self,report_id,shared=None):
		identifier(report_id)
		with self.transaction(shared)as db:
			row=db.execute('SELECT * FROM reports WHERE id = ?',(report_id,)).fetchone()
			if row is None:
				mirrored=self._mirrored_worker_reports().get(report_id)
				if mirrored is not None:return dict(mirrored)
				raise FileNotFoundError('Report not found')
			return dict(row,submission_format=json.loads(row['submission_format'])if row['submission_format']else None)
	@staticmethod
	def validate_fields(fields):
		if not 1<=len(fields)<=MAX_FIELDS:raise ValueError(f"A report holds 1–{MAX_FIELDS} fields")
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
			if len(labels)>1:raise ValueError(f"{field['type']} field '{field_id}' takes at most one free-text option")
	@staticmethod
	def _format_report_submission(report_id,title,fields,answers,submission_format=None):
		if not isinstance(answers,dict):raise TypeError('Answers is a JSON object keyed by field ID')
		known={field['id']for field in fields}
		if set(answers)-known:raise ValueError(f"Unknown field IDs: {', '.join(sorted(set(answers)-known))}")
		metadata=validate_submission_format(submission_format,fields);mappings=metadata.get('fields',{})if metadata is not None else{};lines=[f"REPORT {report_id} {title}:"];slots,answered={},False
		for field in fields:
			field_id=field['id'];value=answers.get(field_id);mapping=mappings.get(field_id,{})if metadata is not None else None
			if value is None or field['type']=='checkbox'and value==[]or field['type']=='text'and isinstance(value,str)and not value.strip():rendered='(skipped)'
			elif field['type']=='text':
				if not isinstance(value,str):raise ValueError(f"{field_id}: text answers must be strings")
				rendered,answered=value,True
			elif field['type']=='choice':_,rendered=formatted_selection(field,value,mapping);answered=True
			else:
				if not isinstance(value,list)or any(not isinstance(item,(str,dict))for item in value):raise ValueError(f"{field_id}: select options only")
				selections=[formatted_selection(field,item,mapping)for item in value]
				if len({key for(key,_)in selections})!=len(value):raise ValueError(f"{field_id}: repeated selections")
				rendered=', '.join(text for(_,text)in selections);answered=True
			question=(mapping or{}).get('question',field['prompt']);slots[f"{field_id}.question"]=question;slots[f"{field_id}.answer"]=rendered;lines.append(f"  {question} ({field_id}): {rendered}")
		if not answered:raise ValueError('A report submission carries at least one answer')
		if metadata is not None and'template'in metadata:lines=[lines[0],expand_submission_template(metadata['template'],slots)]
		return'\n'.join(lines)
	def submit_report(self,report_id,note_id,answers,revision=None):
		if not isinstance(revision,str)or not revision:raise ValueError('Report revision required; copy your entries and reload the preview')
		mirrored=None
		with self.transaction()as db:
			db.execute('BEGIN IMMEDIATE');report=self.report(report_id,shared=db)
			if revision!=report['updated_at']:raise ReportChanged('Report changed. Your entries are kept; copy them before refreshing, reviewing and resending')
			fields=parse_fields(report['markdown'])[1]
			if not fields:raise ValueError('This report has no fields to answer')
			local_sub=self.submit(report_id,report['title'],fields,note_id,answers,shared=db,submission_format=report['submission_format'],revision=revision);fwd=self._forwarded_worker_reports().get(report_id)
			if not report.get('worker_branch')and not(fwd and fwd.get('worker_branch')):return local_sub
			mirrored=report if report.get('worker_branch')else{'worker_branch':fwd['worker_branch'],'worker_report_id':fwd['worker_report_id']}
		self.board_action({'action':'worker-report','target':mirrored['worker_branch'],'op':'answer','report_id':mirrored['worker_report_id'],'answers':answers,'submission_id':local_sub['id'],'from_owner':True});return{**local_sub,'forwarded_to_worker':mirrored['worker_branch']}
	def submit(self,report_id,title,fields,note_id,answers,shared=None,submission_format=None,revision=None):formatted=self._format_report_submission(report_id,title,fields,answers,submission_format=submission_format);data={'revision':revision,'answers':answers}if revision is not None else None;return self.submission(note_id,report_id,formatted,shared=shared,answer_data=data)
def open_link(renderer,tokens,index,options,env):
	token=tokens[index];href=token.attrGet('href')or''
	if href and not href.startswith('#'):token.attrSet('target','_blank');token.attrSet('rel','noopener noreferrer')
	return renderer.renderToken(tokens,index,options,env)
LOCAL_MARKDOWN_IMAGE=re.compile('\\.(?:gif|png|jpe?g|webp)$',re.IGNORECASE)
WIREFRAME_IMAGE=re.compile('-wireframe\\.(?:png|gif|jpe?g|webp)$',re.IGNORECASE)
def normalize_markdown_image_src(source):
	clean=str(source or'').strip()
	if not clean or clean.startswith(('/','#','//'))or re.match('^[a-zA-Z][a-zA-Z0-9+.-]*:',clean):return source
	target=clean.split('?',1)[0].split('#',1)[0]
	if LOCAL_MARKDOWN_IMAGE.search(target):from urllib.parse import quote;return f"/api/report-image?path={quote(target,safe='')}"
	return source
def render_image(renderer,tokens,index,options,env):
	src=tokens[index].attrGet('src')
	if src:tokens[index].attrSet('src',normalize_markdown_image_src(src))
	pending=list(tokens[index].children or[])
	while pending:
		token=pending.pop()
		if token.type in{'text_special','code_inline'}:token.type='text'
		elif token.type=='hardbreak':token.type='softbreak'
		pending.extend(token.children or[])
	return renderer.image(tokens,index,options,env)
def has_renderer():
	global HAS_RENDERER
	if HAS_RENDERER is None:
		try:import markdown_it
		except ImportError:HAS_RENDERER=False
		else:HAS_RENDERER=True
	return HAS_RENDERER
def require_renderer():
	if not has_renderer():raise SystemExit("serve needs markdown-it-py: install it in the preview venv with `python -m pip install markdown-it-py`, then start the server with that venv's Python. read, ack and publish work without it.")
CODE_BLOCK=re.compile('<pre>(.*?)</pre>',re.DOTALL)
CODE_TAG=re.compile('<[^>]+>')
ICON_COPY='<svg class="lucide lucide-copy" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><rect width="14" height="14" x="8" y="8" rx="2" ry="2"></rect><path d="M4 16c-1.1 0-2-.9-2-2V4c0-1.1.9-2 2-2h10c1.1 0 2 .9 2 2"></path></svg>'
ICON_MARKDOWN='<svg class="markdown-icon" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M22.27 19.385H1.73A1.73 1.73 0 0 1 0 17.655V6.345a1.73 1.73 0 0 1 1.73-1.73h20.54A1.73 1.73 0 0 1 24 6.345v11.308a1.73 1.73 0 0 1-1.73 1.731Zm-16.501-3.462v-4.5l2.308 2.885 2.307-2.885v4.5h2.308V8.078h-2.308l-2.307 2.885-2.308-2.885H3.46v7.847Zm15.463-3.923h-2.309V8.077h-2.307V12h-2.308l3.461 4.039Z" fill="currentColor" stroke="none"></path></svg>'
ICON_BRACES='<svg class="lucide lucide-braces" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M8 3H7a2 2 0 0 0-2 2v5a2 2 0 0 1-2 2 2 2 0 0 1 2 2v5c0 1.1.9 2 2 2h1"></path><path d="M16 21h1a2 2 0 0 0 2-2v-5c0-1.1.9-2 2-2a2 2 0 0 1-2-2V5a2 2 0 0 0-2-2h-1"></path></svg>'
MARKDOWN_CODE_TAG=re.compile('^<code\\s+class="language-(?:markdown|md)"\\s*>',re.IGNORECASE)
MERMAID_CODE_TAG=re.compile('^<code\\s+class="language-mermaid"\\s*>',re.IGNORECASE)
CODE_LANG_TAG=re.compile('^<code\\s+class="language-([^\\s"]+)"',re.IGNORECASE)
CODE_LANG_NAMES={'bash':'Bash','c':'C','cjs':'JavaScript','cpp':'C++','cs':'C#','css':'CSS','diff':'Diff','go':'Go','html':'HTML','java':'Java','javascript':'JavaScript','js':'JavaScript','json':'JSON','jsx':'JSX','kt':'Kotlin','markdown':'Markdown','md':'Markdown','mermaid':'Mermaid','mjs':'JavaScript','php':'PHP','plaintext':'Text','py':'Python','python':'Python','rb':'Ruby','rs':'Rust','rust':'Rust','sh':'Shell','shell':'Shell','sql':'SQL','svg':'SVG','swift':'Swift','text':'Text','toml':'TOML','ts':'TypeScript','tsx':'TSX','txt':'Text','typescript':'TypeScript','xml':'XML','yaml':'YAML','yml':'YAML','zsh':'Zsh'}
def format_code_language(inner):
	match=CODE_LANG_TAG.match(inner.strip())
	if not match:return'Code'
	raw=match.group(1).lower();return CODE_LANG_NAMES.get(raw,raw[:1].upper()+raw[1:])
def add_copy_buttons(rendered):
	def replace(match):inner=match.group(1);code=html.unescape(CODE_TAG.sub('',inner));payload=html.escape(code,quote=True).replace('\n','&#10;');md_button=f'<button type="button" class="preview-code-md" aria-label="Preview Markdown" title="Preview Markdown" aria-pressed="false" data-code="{payload}">{ICON_MARKDOWN}</button>'if MARKDOWN_CODE_TAG.match(inner.strip())else'';mermaid_button=f'<button type="button" class="preview-code-mermaid" aria-label="Show Mermaid code" title="Show Mermaid code" aria-pressed="false" data-code="{payload}">{ICON_BRACES}</button>'if MERMAID_CODE_TAG.match(inner.strip())else'';button=f'<button type="button" class="copy-code" aria-label="Copy code" title="Copy code" data-code="{payload}">{ICON_COPY}</button>';lang=html.escape(format_code_language(inner),quote=True);header=f'<div class="code-block-header"><span class="code-block-lang">{ICON_BRACES}<span>{lang}</span></span></div>';return f'<div class="code-block">{md_button}{mermaid_button}{button}{header}<pre>{inner}</pre></div>'
	return CODE_BLOCK.sub(replace,rendered)
ESCAPED_FENCE=re.compile('^(?P<indent>[ \\t]*)\\\\(?P<fence>(?:`{3,}|~{3,}))',re.MULTILINE)
def _unescape_inline_code_line(line):
	out=[];n=len(line);i=0
	while i<n:
		ch=line[i]
		if ch!='`':out.append(ch);i+=1;continue
		run_end=i
		while run_end<n and line[run_end]=='`':run_end+=1
		if run_end-i>1 or i>0 and line[i-1]=='\\':out.append(line[i:run_end]);i=run_end;continue
		if line[i:i+3]=='`\\`'and(i+3==n or line[i+3]!='`'):out.append('`\\`');i+=3;continue
		j=i+1;escaped=0;closing=-1
		while j<n and line[j]!='\n':
			if line[j]=='\\'and j+1<n and line[j+1]=='`'and(j==i+1 or line[j-1]!='\\'):escaped+=1;j+=2;continue
			if line[j]=='`':
				if j+1<n and line[j+1]=='`':closing=-1;break
				closing=j;break
			j+=1
		if closing==-1 or escaped==0:out.append('`');i+=1;continue
		body=line[i+1:closing];buf=[];k=0;m=len(body)
		while k<m:
			if body[k]=='\\'and k+1<m and body[k+1]=='`'and(k==0 or body[k-1]!='\\'):buf.append('`');k+=2
			else:buf.append(body[k]);k+=1
		content=''.join(buf);longest=0;current=0
		for c in content:
			if c=='`':current+=1;longest=max(longest,current)
			else:current=0
		fence='`'*(longest+1)
		if content.startswith('`')or content.endswith('`')or content.startswith(' ')and content.endswith(' '):out.append(f"{fence} {content} {fence}")
		else:out.append(f"{fence}{content}{fence}")
		i=closing+1
	return''.join(out)
def unescape_fences(source):
	restored=ESCAPED_FENCE.sub(lambda match:match.group('indent')+match.group('fence'),source);out,fence=[],None
	for line in restored.splitlines(keepends=True):
		if fence is not None:
			out.append(line)
			if line.strip().startswith(fence):fence=None
			continue
		opening=FENCE.match(line)
		if opening:fence=opening.group(1);out.append(line);continue
		out.append(_unescape_inline_code_line(line))
	return''.join(out)
PARAGRAPH=re.compile('<p>.*?</p>',re.DOTALL)
PARAGRAPH_BREAK=re.compile('<br\\s*/?>')
def drop_paragraph_breaks(rendered):return PARAGRAPH.sub(lambda match:PARAGRAPH_BREAK.sub('',match.group(0)),rendered)
def sized_image(match,validate):
	alt,source,width,height=match.groups();source=normalize_markdown_image_src(source)
	if not validate(source):return None
	size=f' width="{width}"'+(f' height="{height}"'if height else'');return f'<img src="{html.escape(source,quote=True)}" alt="{html.escape(alt,quote=True)}"{size} />'
def highlight_markdown(parser):
	from markdown_it.rules_inline.state_inline import Delimiter
	def tokenize(state,silent):
		if silent or state.src[state.pos]!='=':return False
		scanned=state.scanDelims(state.pos,True);length=scanned.length
		if length<2:return False
		if length%2:state.push('text','',0).content='=';length-=1
		for _ in range(0,length,2):
			state.push('text','',0).content='=='
			if scanned.can_open or scanned.can_close:state.delimiters.append(Delimiter(marker=ord('='),length=0,token=len(state.tokens)-1,end=-1,open=scanned.can_open,close=scanned.can_close))
		state.pos+=scanned.length;return True
	def post_process(state):
		groups=[state.delimiters]+[meta['delimiters']for meta in state.tokens_meta if meta and'delimiters'in meta]
		for delimiters in groups:
			lone=[]
			for start in delimiters:
				if start.marker!=ord('=')or start.end==-1:continue
				end=delimiters[start.end]
				for(index,nesting)in((start.token,1),(end.token,-1)):token=state.tokens[index];token.type='mark_open'if nesting==1 else'mark_close';token.tag,token.nesting='mark',nesting;token.markup,token.content='==',''
				previous=state.tokens[end.token-1]
				if previous.type=='text'and previous.content=='=':lone.append(end.token-1)
			while lone:
				index=lone.pop();end=index+1
				while end<len(state.tokens)and state.tokens[end].type=='mark_close':end+=1
				end-=1
				if index!=end:state.tokens[index],state.tokens[end]=state.tokens[end],state.tokens[index]
	parser.inline.ruler.before('emphasis','mark',tokenize);parser.inline.ruler2.before('emphasis','mark',post_process)
def report_marker_warnings(markdown):
	from markdown_it import MarkdownIt;parser=MarkdownIt('commonmark',{'html':False}).enable(['table','strikethrough']);highlight_markdown(parser);parser.disable('text_join');lines,findings=markdown.splitlines(),set()
	for token in parser.parse(unescape_fences(markdown)):
		if token.type!='inline'or not token.map:continue
		start,end=token.map;raw,cursor='\n'.join(lines[start:end]),0;pieces=[]
		for child in token.children or():
			if child.type=='code_inline':ticks=re.compile('(?<!`)'+re.escape(child.markup)+'(?!`)');opening=ticks.search(raw,cursor);closing=ticks.search(raw,opening.end())if opening else None;cursor=closing.end()if closing else len(raw);pieces.append((cursor,'X'));continue
			if child.type in{'softbreak','hardbreak'}:
				newline=raw.find('\n',cursor)
				if newline>=0:cursor=newline+1
				pieces.append((cursor,'\n'));continue
			content=child.markup if child.type in{'text_special','s_open','s_close','mark_open','mark_close'}else child.content
			if not content:continue
			offset=raw.find(content,cursor)
			if offset<0 and end-start>1:continue
			offset=max(0,offset);cursor=offset+len(content)
			if child.type=='text':pieces.append((offset,content))
			else:pieces.append((offset,'X'*len(content)))
		combined=''.join(text for(_,text)in pieces);offsets=[off for(off,text)in pieces for _ in text]
		for marker in('~~','=='):
			pattern=re.escape(marker)+'(.+?)'+re.escape(marker)
			for match in re.finditer(pattern,combined):raw_offset=offsets[match.start()]if offsets else 0;line=start+1+raw.count('\n',0,raw_offset);findings.add((line,marker))
	return[{'line':line,'marker':marker}for(line,marker)in sorted(findings)]
def render(markdown,breaks=False):
	try:from markdown_it import MarkdownIt;from markdown_it.common.normalize_url import validateLink
	except ImportError as error:raise RuntimeError("Markdown rendering needs markdown-it-py. Install it in the preview's venv and restart the server with that venv's Python; steering still works.")from error
	parser=MarkdownIt('commonmark',{'html':False,'breaks':breaks}).enable(['table','strikethrough']);highlight_markdown(parser);parser.add_render_rule('link_open',open_link);parser.add_render_rule('image',render_image);tagged,images=hold_out(strip_fenced_md_comments(markdown),SIZED_IMAGE,lambda match:sized_image(match,validateLink),'image');rendered=drop_paragraph_breaks(parser.render(unescape_fences(tagged)));return add_copy_buttons(put_back(rendered,images,'image'))
def file_signature(paths):
	entries=[]
	for path in paths:
		target=Path(path)
		try:stat=target.stat();entries.append((str(target),stat.st_mtime_ns,stat.st_size))
		except OSError:entries.append((str(target),None,None))
	return tuple(entries)
def assets_signature(assets_dir=None):base=Path(assets_dir)if assets_dir is not None else ASSETS;return file_signature(base/name for name in('index.html','style.css','app.js','message-markdown.js','markdown-it.min.js','mermaid.min.js'))
def scripts_signature(scripts_dir=None):base=Path(scripts_dir)if scripts_dir is not None else Path(__file__).resolve().parent;return file_signature(base/name for name in('preview.py','survey.py'))
def handler(store,reload_event=None):
	from http.server import BaseHTTPRequestHandler
	if reload_event is not None and not os.environ.get('PYTEST_CURRENT_TEST'):ensure_workspace_watcher(store)
	survey_state={'results':None,'answered_at':None,'requested':False};survey_lock=threading.Lock()
	class Handler(BaseHTTPRequestHandler):
		def setup(self):super().setup();self.connection.settimeout(15)
		def reply(self,status,body,content_type='application/json; charset=utf-8',filename=None):
			data=body if isinstance(body,(bytes,bytearray))else body.encode('utf-8')
			if getattr(self,'ws_request_id',None)is not None:self.ws_send(1,json.dumps({'type':'response','id':self.ws_request_id,'status':status,'body':data.decode('utf-8'),'contentType':content_type}).encode());return
			self.send_response(status);self.send_header('Content-Type',content_type);self.send_header('Content-Length',str(len(data)));self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff');self.send_header('Content-Security-Policy',"default-src 'none'; script-src 'self' 'unsafe-inline'; style-src 'unsafe-inline'; font-src 'self' data:; img-src 'self' data: https: blob:; connect-src 'self' https: ws: wss:; base-uri 'none'; form-action 'self'")
			if filename:safe=header_filename(filename);self.send_header('Content-Disposition',f'attachment; filename="{safe}"')
			self.end_headers();self.wfile.write(data)
		def problem(self,status,error):self.reply(status,json.dumps({'error':str(error)}))
		def ws_send(self,opcode,data):size=len(data);length=bytes([size])if size<126 else b'~'+size.to_bytes(2,'big')if size<65536 else b'\x7f'+size.to_bytes(8,'big');self.connection.sendall(bytes([128|opcode])+length+data)
		def ws_dispatch(self,value):
			import io;from email.message import Message
			if not isinstance(value,dict):raise TypeError('Expected an object')
			if value.get('type')=='pong':return
			request_id=value.get('id');method,path=value.get('method','GET'),value.get('path','');body,headers=value.get('body',''),value.get('headers',{})
			if type(request_id)not in(str,int)or len(str(request_id))>80 or method not in('GET','POST')or not isinstance(path,str)or not path.startswith('/api/')or urlsplit(path).path=='/api/ws'or not isinstance(body,str)or not isinstance(headers,dict):raise ValueError('Invalid WebSocket request')
			fields=Message()
			for name in('Content-Type','X-Fetch-Claim','X-Fetch-Source','X-Timezone'):
				item=headers.get(name)
				if item is not None:
					if not isinstance(item,str)or'\r'in item or'\n'in item:raise ValueError('Invalid request header')
					fields[name]=item
			data=body.encode('utf-8');fields['Content-Length']=str(len(data));original=self.path,self.rfile,self.headers;self.path,self.rfile,self.headers=path,io.BytesIO(data),fields;self.ws_request_id=request_id
			try:(self.do_GET if method=='GET'else self.do_POST)()
			finally:self.path,self.rfile,self.headers=original;del self.ws_request_id
		def websocket(self):
			try:origin=urlsplit(self.headers.get('Origin',''))
			except ValueError:self.problem(403,'Invalid WebSocket origin');return
			if origin.scheme not in('http','https')or origin.netloc.lower()!=self.headers.get('Host','').lower():self.problem(403,'WebSocket origin must match the preview');return
			if self.headers.get('Upgrade','').lower()!='websocket'or'upgrade'not in{part.strip()for part in self.headers.get('Connection','').lower().split(',')}or self.headers.get('Sec-WebSocket-Version')!='13':self.problem(400,'Expected a version 13 WebSocket upgrade');return
			key=self.headers.get('Sec-WebSocket-Key','')
			try:
				if len(base64.b64decode(key,validate=True))!=16:raise ValueError('Invalid WebSocket key')
			except ValueError as error:self.problem(400,error);return
			self.protocol_version='HTTP/1.1';self.send_response(101);self.send_header('Upgrade','websocket');self.send_header('Connection','Upgrade');self.send_header('Sec-WebSocket-Accept',base64.b64encode(hashlib.sha1((key+'258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode(),usedforsecurity=False).digest()).decode());self.end_headers();self.close_connection=True;self.connection.settimeout(WS_TICK_SECONDS);buffer,message=bytearray(),bytearray();fragmented=False;last_seen=last_heartbeat=time.monotonic()
			try:
				with closing(store.connect())as db:
					version=db.execute('PRAGMA data_version').fetchone()[0];asset_ver=assets_signature();self.ws_send(1,b'{"type":"change"}')
					while True:
						try:
							chunk=self.connection.recv(65536)
							if not chunk:return
							buffer.extend(chunk);last_seen=time.monotonic()
						except TimeoutError:pass
						while len(buffer)>=2:
							first,second=buffer[:2];opcode,final=first&15,bool(first&128)
							if first&112 or not second&128 or opcode not in(0,1,2,8,9,10):raise ValueError('Invalid frame')
							size,offset=second&127,2
							if size in(126,127):
								width=2 if size==126 else 8
								if len(buffer)<2+width:break
								size=int.from_bytes(buffer[2:2+width],'big');offset+=width
								if size<(126 if width==2 else 65536)or size>=2**63:raise ValueError('Invalid frame length')
							if opcode>=8 and(not final or size>125):raise ValueError('Invalid control frame')
							if size+(len(message)if opcode<8 else 0)>WS_MAX_MESSAGE:self.ws_send(8,(1009).to_bytes(2,'big'));return
							if len(buffer)<offset+4+size:break
							mask=buffer[offset:offset+4];data=bytes(v^mask[i%4]for(i,v)in enumerate(buffer[offset+4:offset+4+size]));del buffer[:offset+4+size]
							if opcode==8:
								if len(data)==1:raise ValueError('Invalid close frame')
								if data:
									code=int.from_bytes(data[:2],'big')
									if code not in(1000,1001,1002,1003,1007,1008,1009,1010,1011,1012,1013,1014)and not 3000<=code<5000:raise ValueError('Invalid close code')
									data[2:].decode('utf-8')
								self.ws_send(8,data);return
							if opcode==9:self.ws_send(10,data);continue
							if opcode==10:continue
							if opcode==2:self.ws_send(8,(1003).to_bytes(2,'big'));return
							if(opcode==0)!=fragmented:raise ValueError('Invalid continuation')
							message.extend(data);fragmented=not final
							if final:self.ws_dispatch(json.loads(message.decode('utf-8')));message.clear()
						current=db.execute('PRAGMA data_version').fetchone()[0]
						if current!=version:version=current;self.ws_send(1,b'{"type":"change"}')
						current_assets=assets_signature()
						if current_assets!=asset_ver:asset_ver=current_assets;self.ws_send(1,b'{"type":"reload"}')
						if reload_event is not None and reload_event.is_set():self.ws_send(1,b'{"type":"reload"}');self.ws_send(8,(1012).to_bytes(2,'big'));return
						now=time.monotonic()
						if now-last_seen>WS_IDLE_SECONDS:self.ws_send(8,(1001).to_bytes(2,'big'));return
						if now-last_heartbeat>=WS_HEARTBEAT_SECONDS:self.ws_send(1,b'{"type":"heartbeat"}');last_heartbeat=now
			except(ValueError,TypeError)as error:
				code=1007 if isinstance(error,UnicodeDecodeError)else 1002
				try:self.ws_send(8,code.to_bytes(2,'big'))
				except OSError:pass
			except(OSError,sqlite_api().Error):return
		def record_userscript_version(self):
			header_ver=(self.headers.get(USERSCRIPT_VERSION_HEADER)or'').strip()
			if header_ver:store.set_userscript_version(header_ver)
		def do_GET(self):
			path=urlsplit(self.path).path
			try:
				if path=='/api/ws':self.websocket();return
				if path.startswith('/api/'):self.record_userscript_version()
				if path=='/':page=(ASSETS/'index.html').read_text(encoding='utf-8');page=page.replace('<title>Workspace · Messages & reports</title>',f"<title>{html.escape(store.repository_root().name)} - Steering</title>");page=page.replace('__STYLE__',(ASSETS/'style.css').read_text(encoding='utf-8'));page=page.replace('__MARKDOWN_SCRIPT__',(ASSETS/'markdown-it.min.js').read_text(encoding='utf-8'));page=page.replace('__SCRIPT__',(ASSETS/'message-markdown.js').read_text(encoding='utf-8')+'\n'+(ASSETS/'app.js').read_text(encoding='utf-8'));self.reply(200,page,'text/html; charset=utf-8');return
				if path=='/assets/mermaid.min.js':
					bundle=ASSETS/'mermaid.min.js'
					if not bundle.is_file():self.problem(404,'Not found');return
					self.reply(200,bundle.read_bytes(),'text/javascript; charset=utf-8');return
				font_match=re.fullmatch('/assets/(Basel_Grotesk_(?:Book|Regular|Medium|Bold|Mono_Regular)\\.woff2)',path)
				if font_match:self.reply(200,(ASSETS/font_match.group(1)).read_bytes(),'font/woff2');return
				if path=='/api/survey':
					checked_at=clip_stamp(store.meta_value('survey_checked_at'))
					with survey_lock:
						if survey_state['results']is not None and checked_at and(not survey_state['answered_at']or checked_at>survey_state['answered_at']):survey_state['answered_at']=checked_at;survey_state['requested']=False
						body=json.dumps({'results':survey_state['results'],'answered_at':survey_state['answered_at']})
					self.reply(200,body);return
				if path=='/api/userscript-autosave':
					params=parse_qs(urlsplit(self.path).query);since_raw=(params.get('since')or[''])[0].strip();mode=(params.get('mode')or['full'])[0].strip().lower()or'full';since=None
					if since_raw:
						try:since=int(since_raw)
						except ValueError:self.problem(400,'since must be an integer');return
					try:snapshot=store.userscript_snapshot(since=since,mode=mode)
					except(ValueError,TypeError)as error:self.problem(400,error);return
					self.reply(200,json.dumps(snapshot,ensure_ascii=False));return
				if path=='/api/board':
					params=parse_qs(urlsplit(self.path).query);since_raw=(params.get('since')or[''])[0].strip();mode=(params.get('mode')or['full'])[0].strip().lower()or'full';since=None
					if since_raw:
						try:since=int(since_raw)
						except ValueError:self.problem(400,'since must be an integer');return
					self.reply(200,json.dumps(store.board_state_sync(since=since,mode=mode),ensure_ascii=False));return
				if path=='/api/poll':is_poll=store.polling();self.reply(200,json.dumps({'polling':is_poll,'polling_since':clip_stamp(store.meta_value(POLL_SINCE_META))if is_poll else None},ensure_ascii=False));return
				if path=='/api/state':
					header_tz=(self.headers.get('X-Timezone')or'').strip()
					if header_tz and zone_exists(header_tz)and store.meta_value(TIMEZONE_META)!=header_tz:store.set_display_timezone(header_tz)
					state=store.state();state['slash_commands']=discover_slash_commands()
					try:
						for item in state['notes']+[ack for report in state['reports']for ack in report['acknowledgements']]+((state.get('board')or{}).get('entries')or[]):
							if'text'in item:item['html']=render(item['text'])
							if item.get('ack_type')=='reply'and item.get('ack_text'):item['ack_html']=render(item['ack_text'].replace('\\n','\n'))
							for reply in item.get('replies')or[]:
								if reply['type']=='reply':reply['html']=render(reply['text'].replace('\\n','\n'))
					except RuntimeError as error:state['rendering_error']=str(error)
					self.reply(200,json.dumps(state,ensure_ascii=False));return
				if path=='/api/submissions':live={report['id']for report in store.state()['reports']};self.reply(200,json.dumps([saved_answer_line(record)for record in store.submissions()if record['report_id']in live],ensure_ascii=False),'application/json; charset=utf-8');return
				if path=='/api/copy-state':notes=store.state()['notes'];lines,counts=store.state_lines(notes,store.tasks()or{});stamps=store.newest_stamps();self.reply(200,json.dumps({'text':state_ndjson(lines),'counts':counts,'stamp':store.newest_stamp(stamps),'stamps':{'note':stamps['note'],'task':stamps['task']},'repo':store.path.parent.parent.name,'branch':workspace_branch(store.path.parent.parent)},ensure_ascii=False),'application/json; charset=utf-8');return
				if path=='/api/report-image':
					from urllib.parse import parse_qsl;query=dict(parse_qsl(urlsplit(self.path).query));rel_target=query.get('path','')
					try:mime,raw_bytes,_=resolve_report_image(rel_target,store.repository_root())
					except ValueError as error:self.problem(404,error);return
					self.reply(200,raw_bytes,mime);return
				if path=='/api/report-sources':self.reply(200,json.dumps([saved_report_line(record)for record in store.report_sources()],ensure_ascii=False),'application/json; charset=utf-8');return
				match=re.fullmatch('/api/reports/([a-zA-Z0-9_-]{1,80})/(html|source)',path)
				if match:
					report_id,type=match.groups();report=store.report(report_id)
					if type=='html':
						try:body,questions=render_report(report['markdown'],title=report.get('title'))
						except ValueError as error:self.problem(500,f"This report cannot be rendered: {error}");return
						self.reply(200,json.dumps({'html':body,'fields':len(questions),'warnings':report_marker_warnings(report['markdown']),'revision':report['updated_at'],'published':report['published_at']or report['updated_at'],'edited':report['updated_at']},ensure_ascii=False));return
					self.reply(200,report['markdown'],'text/plain; charset=utf-8',f"{report_id}.md");return
				self.problem(404,'Not found')
			except FileNotFoundError as error:self.problem(404,error)
			except(OSError,sqlite_api().Error,RuntimeError)as error:self.problem(503,error)
		def do_POST(self):
			path=urlsplit(self.path).path;autosave_post=path in{'/api/userscript-autosave','/api/userscript-autosave/compare','/api/userscript-autosave/offer'};report_submit=re.fullmatch('/api/reports/([a-zA-Z0-9_-]{1,80})/submit',path);report_seen=re.fullmatch('/api/reports/([a-zA-Z0-9_-]{1,80})/seen',path);report_ack_seen=re.fullmatch('/api/reports/([a-zA-Z0-9_-]{1,80})/ack-seen',path);report_view=re.fullmatch('/api/reports/([a-zA-Z0-9_-]{1,80})/view',path);message_replies_seen=re.fullmatch('/api/messages/([a-zA-Z0-9_-]{1,80})/replies/seen',path);report_unpublish=re.fullmatch('/api/reports/([a-zA-Z0-9_-]{1,80})/unpublish',path);fetch_post=re.fullmatch('/api/fetch-jobs/([a-zA-Z0-9_-]{1,80})/(renew|result|fail|retry|approve|deny|drop)',path);note_upload=path=='/api/notes/with-file';staged_upload=path=='/api/uploads';upload_drop=re.fullmatch('/api/uploads/([a-zA-Z0-9_-]{1,80})/drop',path);agent_key_post=path=='/api/key';timezone_post=path=='/api/timezone';skip_poll_post=path=='/api/skip-poll';interrupt_post=path=='/api/agent-interrupt';task_order_post=path=='/api/tasks/order';fetch_result=bool(fetch_post and fetch_post.group(2)=='result')
			if path not in{'/api/notes','/api/markdown','/api/slash','/api/fetch-jobs','/api/fetch-jobs/claim','/api/survey','/api/survey/request','/api/board','/api/poll'}and not report_submit and not report_seen and not report_ack_seen and not report_view and not message_replies_seen and not report_unpublish and not note_upload and not staged_upload and not upload_drop and not fetch_post and not agent_key_post and not timezone_post and not skip_poll_post and not interrupt_post and not task_order_post and not autosave_post:self.problem(404,'Not found');return
			self.record_userscript_version();content_type=self.headers.get('Content-Type','')
			if note_upload or staged_upload:
				if not content_type.lower().startswith('multipart/form-data;'):self.problem(415,'Expected multipart/form-data');return
			elif content_type!='application/json'and not fetch_result:self.problem(415,'Expected application/json');return
			try:
				if self.headers.get('Transfer-Encoding')or len(self.headers.get_all('Content-Length',[]))!=1:raise ValueError('Send one Content-Length and no Transfer-Encoding')
				length=int(self.headers.get('Content-Length','0'))
				if fetch_result:
					with closing(store.connect())as db:store.claimed_fetch(db,fetch_post.group(1),self.headers.get('X-Fetch-Claim',''))
				if length<=0:subject='Upload'if note_upload else'Download'if fetch_result else'Request body';self.problem(413,f"{subject} has an invalid length");return
				if staged_upload:
					with stream_note_attachments(content_type,self.rfile,length,staged=True)as parsed:upload_id,name,type,data=parsed;record=store.stage_upload(name,type,data,upload_id)
					self.reply(201,json.dumps(record,ensure_ascii=False));return
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
				if path=='/api/survey':
					import survey;raw_text=data.decode('utf-8');results=list(survey.grade(raw_text));ordered=survey.submission_order(raw_text);results.append({'id':'S0R7D','correct':ordered,'confidence':1,'answer':{'sorted':ordered}})
					with survey_lock:survey_state['results']=results;survey_state['answered_at']=now();survey_state['requested']=False;store.set_meta('survey_state_json',json.dumps(survey_state))
					self.reply(200,json.dumps({'ok':True}));return
				payload=json.loads(data)
				if not isinstance(payload,dict):self.problem(400,'Expected a JSON object');return
				if path=='/api/board':board_result=store.board_action(payload);board_status=409 if isinstance(board_result,dict)and board_result.get('need_full')else 200;self.reply(board_status,json.dumps(board_result,ensure_ascii=False));return
				if path=='/api/poll':
					active=bool(payload.get('active'))
					if active:store.start_poll()
					else:store.clear_polling()
					is_poll=store.polling();self.reply(200,json.dumps({'ok':True,'polling':is_poll,'polling_since':clip_stamp(store.meta_value(POLL_SINCE_META))if is_poll else None},ensure_ascii=False));return
				if path=='/api/survey/request':
					if payload:raise ValueError('Survey request requires an empty object')
					with survey_lock:
						with closing(store.connect())as db:pending=db.execute("SELECT id FROM notes WHERE quiet = 1 AND blocking = 1 AND type IN ('', 'survey') AND acknowledged_at IS NULL LIMIT 1").fetchone()
						if pending is None:
							if survey_state['results']is None:import survey;store.note(uuid.uuid4().hex[:6],f"Ack this note, then submit fresh answers via arena-preview survey.\n{survey.INSTRUCTIONS}",quiet=True,blocking=True,type='survey')
							else:store.schedule_read_set_note(manual=True)
						survey_state['requested']=True
					self.reply(200,json.dumps({'ok':True}));return
				if autosave_post:
					if path.endswith('/compare'):result=store.compare_userscript_save(payload)
					elif path.endswith('/offer'):result=store.offer_userscript_backup(payload)
					else:result=store.confirm_userscript_save(payload)
					self.reply(200,json.dumps(result,ensure_ascii=False));return
				if agent_key_post:
					candidate=payload.get('key')
					if not isinstance(candidate,str)or not AGENT_KEY_RE.fullmatch(candidate):raise ValueError('key must be 20 to 64 letters, digits, dashes or underscores')
					host=payload.get('host')
					if host is not None and(not isinstance(host,str)or not AGENT_HOST_RE.fullmatch(host)):raise ValueError('host must be one https origin, with no path')
					store.set_agent_key(candidate,host)
					if payload.get('egress_version')is not None:store.set_egress_version(payload.get('egress_version'))
					self.reply(200,json.dumps(store.agent_key(),ensure_ascii=False));return
				if interrupt_post:
					armed=payload.get('armed',True)
					if not isinstance(armed,bool):raise ValueError('armed must be true or false')
					self.reply(200,json.dumps(store.request_interrupt(payload.get('tab'),payload.get('report_id'),armed,reason=payload.get('reason'),clause_file=payload.get('clause_file'),note=payload.get('note')),ensure_ascii=False));return
				if skip_poll_post:
					armed=payload.get('skip',True)
					if not isinstance(armed,bool):raise ValueError('skip must be true or false')
					self.reply(200,json.dumps(store.request_skip_poll(armed),ensure_ascii=False));return
				if task_order_post:self.reply(200,json.dumps(store.reorder_tasks(payload.get('ids')),ensure_ascii=False));return
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
				if path=='/api/slash':
					raw_slash=owner_text(payload.get('text'));resolved=resolve_slash_command(raw_slash,store.repository_root())
					if resolved is None:resolved={'mode':'passthrough','text':raw_slash}
					elif resolved.get('mode')=='query':
						try:resolved['html']=render(resolved['markdown'])
						except RuntimeError:pass
					self.reply(200,json.dumps(resolved,ensure_ascii=False));return
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
				if upload_drop:self.reply(200,json.dumps(store.drop_staged(upload_drop.group(1)),ensure_ascii=False));return
				raw_note_text=payload.get('text')
				if isinstance(raw_note_text,str):
					slash_note=resolve_slash_command(raw_note_text,store.repository_root())
					if slash_note and slash_note.get('mode')=='note':raw_note_text=slash_note['text']
				if payload.get('upload_ids')is not None:note=store.note_with_staged(payload.get('id'),raw_note_text,payload.get('upload_ids'))
				else:note=store.note(payload.get('id'),raw_note_text,quiet=bool(payload.get('quiet')))
				for key in('at','acknowledged_at','ack_edited_at','seen_at'):note[key]=clip_stamp(note[key])
				self.reply(201,json.dumps(note,ensure_ascii=False))
			except(ReportChanged,FetchChanged,ReportHeld)as error:self.problem(409,error)
			except FileNotFoundError as error:self.problem(404,error)
			except(ValueError,TypeError,UnicodeDecodeError)as error:self.problem(400,error)
			except(OSError,sqlite_api().Error,RuntimeError)as error:self.problem(503,error)
	return Handler
def resolve_repo_root():
	for parent in Path(__file__).resolve().parents:
		if(parent/'.git').exists():return str(parent)
	return str(Path.cwd())
def print_skills():
	try:import yaml
	except ImportError as error:raise RuntimeError('PyYAML required. Rerun preview installer.')from error
	root=Path(resolve_repo_root());skills={}
	for(type,directory)in(('installed',root/'.agents'/'skills'),('repository',root/'skills')):
		for path in sorted(directory.glob('*/SKILL.md')):
			if type=='repository'and path.parent.name=='refs':continue
			lines=path.read_text(encoding='utf-8-sig').splitlines()
			try:
				if not lines or lines[0].strip()!='---':raise ValueError('missing YAML frontmatter')
				end=next(i for i in range(1,len(lines))if lines[i].strip()=='---');metadata=yaml.safe_load('\n'.join(lines[1:end]))
				if not isinstance(metadata,dict)or not all(isinstance(metadata.get(key),str)and metadata[key].strip()for key in('name','description')):raise ValueError('name and description must be nonempty text')
			except(ValueError,StopIteration,yaml.YAMLError)as error:raise ValueError(f"Invalid skill metadata in {path}: {error}")from error
			name=metadata['name'];skills.setdefault(name.casefold(),(name,type,path,metadata['description']))
	for(name,type,path,description)in skills.values():print(f"{name} [{type}] {path}\n{description}\n")
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
def image_payload(path:Path)->tuple:
	if not path.is_file():raise ValueError(f"{path} is not a file")
	size=path.stat().st_size
	if size>IMAGE_DATA_URI_MAX_BYTES:raise ValueError(f"{path} is {size} bytes; the data URI cap is {IMAGE_DATA_URI_MAX_BYTES} bytes")
	try:
		with path.open('rb')as image:data=image.read(IMAGE_DATA_URI_MAX_BYTES+1)
	except OSError as error:raise ValueError(f"Cannot read {path}: {error.strerror or error}")from None
	if len(data)>IMAGE_DATA_URI_MAX_BYTES:raise ValueError(f"{path} exceeds the data URI cap of {IMAGE_DATA_URI_MAX_BYTES} bytes")
	if data.startswith(b'\x89PNG\r\n\x1a\n'):mime='image/png'
	elif data.startswith(b'\xff\xd8\xff'):mime='image/jpeg'
	elif data.startswith((b'GIF87a',b'GIF89a')):mime='image/gif'
	elif data.startswith(b'RIFF')and data[8:12]==b'WEBP':mime='image/webp'
	else:raise ValueError(f"{path} is not a supported image; the allowed types are PNG, JPEG, GIF and WebP")
	encoded=base64.b64encode(data).decode('ascii');return mime,encoded
def image_data_uri(path:Path)->str:mime,encoded=image_payload(path);alt=path.stem.replace('\\','\\\\').replace('[','\\[').replace(']','\\]');return f"![{alt}](data:{mime};base64,{encoded})"
OUTLINE_MAX_BYTES=1000000
OUTLINE_MAX_ENTRIES=200
PARSER_TOOL_DIR_ENV='ARENA_PREVIEW_PARSER_DIR'
PARSER_PACKAGES={'acorn':'8.18.0','css-tree':'3.2.1'}
PARSER_MANIFEST={'name':'arena-preview-parsers','private':True,'dependencies':dict(PARSER_PACKAGES)}
PARSER_LOCKFILE={'name':'arena-preview-parsers','lockfileVersion':3,'requires':True,'packages':{'':{'name':'arena-preview-parsers','dependencies':dict(PARSER_PACKAGES)},'node_modules/acorn':{'version':'8.18.0','resolved':'https://registry.npmjs.org/acorn/-/acorn-8.18.0.tgz','integrity':'sha512-lGq+9yr1/GuAWaVYIHRjvvySG5/4VfKIvC8EWxStPdcDh/Ka7FG3twP6v4d5BkravUilhIAsG4Qj83t02LWUPQ==','license':'MIT','bin':{'acorn':'bin/acorn'},'engines':{'node':'>=0.4.0'}},'node_modules/css-tree':{'version':'3.2.1','resolved':'https://registry.npmjs.org/css-tree/-/css-tree-3.2.1.tgz','integrity':'sha512-X7sjQzceUhu1u7Y/ylrRZFU2FS6LRiFVp6rKLPg23y3x3c3DOKAwuXGDp+PAGjh6CSnCjYeAul8pcT8bAl+lSA==','license':'MIT','dependencies':{'mdn-data':'2.27.1','source-map-js':'^1.2.1'},'engines':{'node':'^10 || ^12.20.0 || ^14.13.0 || >=15.0.0'}},'node_modules/mdn-data':{'version':'2.27.1','resolved':'https://registry.npmjs.org/mdn-data/-/mdn-data-2.27.1.tgz','integrity':'sha512-9Yubnt3e8A0OKwxYSXyhLymGW4sCufcLG6VdiDdUGVkPhpqLxlvP5vl1983gQjJl3tqbrM731mjaZaP68AgosQ==','license':'CC0-1.0'},'node_modules/source-map-js':{'version':'1.2.2','resolved':'https://registry.npmjs.org/source-map-js/-/source-map-js-1.2.2.tgz','integrity':'sha512-KGj/8Y43x35aZVDtt+J4mK1hoLGHULMYfSkODJNQjNDC3oW1PqPoxMwo0pLUsWM/UEGzON/NxeHywEfNXNP3Vw==','license':'BSD-3-Clause','engines':{'node':'>=0.10.0'}}}}
def parser_tool_dir(tool_dir=None):
	if tool_dir is not None:return Path(tool_dir).resolve()
	configured=os.environ.get(PARSER_TOOL_DIR_ENV,'').strip()
	if configured:return Path(configured).resolve()
	return(Path.home()/'.agents'/'.arena-preview-parsers').resolve()
def _check_parser_package(base_dir,package_name,expected_version):
	pkg_dir=Path(base_dir)/'node_modules'/package_name;pkg_json=pkg_dir/'package.json'
	if not pkg_json.is_file():return None,None
	try:meta=json.loads(pkg_json.read_text(encoding='utf-8'))
	except(OSError,UnicodeDecodeError,json.JSONDecodeError):return None,None
	version_str=meta.get('version');main_rel=meta.get('main')or'index.js';entry=(pkg_dir/main_rel).resolve()
	if version_str!=expected_version or not entry.is_file():return None,version_str
	return str(entry),version_str
def discover_portable_parsers(tool_dir=None):target=parser_tool_dir(tool_dir);acorn_entry,acorn_ver=_check_parser_package(target,'acorn',PARSER_PACKAGES['acorn']);csstree_entry,csstree_ver=_check_parser_package(target,'css-tree',PARSER_PACKAGES['css-tree']);available=bool(acorn_entry and csstree_entry);message=f"Pinned Acorn {acorn_ver} and CSS-tree {csstree_ver} ready in {target}"if available else f"Local Acorn/CSS-tree parsers are not installed in {target}; rerun .agents/skills/arena-skill/scripts/install.sh with Node and npm available.";return{'available':available,'directory':str(target),'acorn':acorn_entry,'css_tree':csstree_entry,'versions':{'acorn':acorn_ver,'css-tree':csstree_ver},'message':message}
def _outline_repo_root(repo_root=None):
	if repo_root is not None:return Path(repo_root).resolve()
	cwd=Path.cwd().resolve()
	for candidate in(cwd,*cwd.parents):
		if(candidate/'.git').exists():return candidate
	return Path(resolve_repo_root()).resolve()
def _resolve_outline_source(path,repo_root=None):
	root=_outline_repo_root(repo_root);raw=Path(path);candidate=raw if raw.is_absolute()else Path.cwd()/raw
	if not raw.is_absolute()and not candidate.exists()and not candidate.is_symlink():
		alt=root/raw
		if alt.exists()or alt.is_symlink():candidate=alt
	lexical=Path(os.path.abspath(candidate))
	try:lexical.relative_to(root)
	except ValueError:raise ValueError(f"{path} is outside the repository checkout ({root})")from None
	resolved=candidate.resolve()
	try:rel_resolved=resolved.relative_to(root)
	except ValueError:raise ValueError(f"{path} resolves outside the repository checkout ({root})")from None
	if not resolved.exists()or not resolved.is_file():raise ValueError(f"{path} is not an existing file")
	size=resolved.stat().st_size
	if size>OUTLINE_MAX_BYTES:raise ValueError(f"{rel_resolved.as_posix()} is {size} bytes; the maximum is {OUTLINE_MAX_BYTES} bytes")
	with resolved.open('rb')as handle:data=handle.read(OUTLINE_MAX_BYTES+1)
	if len(data)>OUTLINE_MAX_BYTES:raise ValueError(f"{rel_resolved.as_posix()} exceeds {OUTLINE_MAX_BYTES} bytes")
	try:text=data.decode('utf-8')
	except UnicodeDecodeError as error:raise ValueError(f"{rel_resolved.as_posix()} is not valid UTF-8: {error}")from None
	return rel_resolved.as_posix(),resolved,text
def _format_python_arguments(ast_mod,args):
	parts=[];pos=list(args.posonlyargs)+list(args.args);defaults=[None]*(len(pos)-len(args.defaults))+list(args.defaults)
	for(index,(arg,default))in enumerate(zip(pos,defaults)):
		piece=arg.arg
		if arg.annotation is not None:piece+=f": {ast_mod.unparse(arg.annotation)}"
		if default is not None:piece+=f" = {ast_mod.unparse(default)}"
		parts.append(piece)
		if args.posonlyargs and index+1==len(args.posonlyargs):parts.append('/')
	if args.vararg is not None:
		vararg=f"*{args.vararg.arg}"
		if args.vararg.annotation is not None:vararg+=f": {ast_mod.unparse(args.vararg.annotation)}"
		parts.append(vararg)
	elif args.kwonlyargs:parts.append('*')
	for(arg,default)in zip(args.kwonlyargs,args.kw_defaults):
		piece=arg.arg
		if arg.annotation is not None:piece+=f": {ast_mod.unparse(arg.annotation)}"
		if default is not None:piece+=f" = {ast_mod.unparse(default)}"
		parts.append(piece)
	if args.kwarg is not None:
		kwarg=f"**{args.kwarg.arg}"
		if args.kwarg.annotation is not None:kwarg+=f": {ast_mod.unparse(args.kwarg.annotation)}"
		parts.append(kwarg)
	return', '.join(parts)
def _outline_python(rel_path,text):
	import ast as ast_mod
	try:tree=ast_mod.parse(text,filename=rel_path)
	except SyntaxError as error:line=error.lineno or 1;col=error.offset or 1;raise ValueError(f"{rel_path}:{line}:{col}: Python syntax error: {error.msg}")from None
	entries=[]
	def walk(body,stack,in_class=False):
		for node in body:
			if isinstance(node,(ast_mod.ClassDef,ast_mod.FunctionDef,ast_mod.AsyncFunctionDef)):
				qual_stack=[*stack,node.name];qualified='.'.join(qual_stack);decorators=[f"@{ast_mod.unparse(item)}"for item in node.decorator_list];start_line=min([node.lineno,*(item.lineno for item in node.decorator_list)]);end_line=getattr(node,'end_lineno',None)or node.lineno
				if isinstance(node,ast_mod.ClassDef):symbol_type='class';bases=[ast_mod.unparse(base)for base in node.bases]+[f"{kw.arg}={ast_mod.unparse(kw.value)}"if kw.arg else f"**{ast_mod.unparse(kw.value)}"for kw in node.keywords];signature=f"class {node.name}({', '.join(bases)})"if bases else f"class {node.name}"
				else:
					is_async=isinstance(node,ast_mod.AsyncFunctionDef)
					if in_class:symbol_type='async_method'if is_async else'method'
					else:symbol_type='async_function'if is_async else'function'
					prefix='async def'if is_async else'def';params=_format_python_arguments(ast_mod,node.args);returns=f" -> {ast_mod.unparse(node.returns)}"if node.returns is not None else'';signature=f"{prefix} {node.name}({params}){returns}"
				entries.append({'type':symbol_type,'name':node.name,'qualified_name':qualified,'signature':signature,'decorators':decorators,'line':start_line,'def_line':node.lineno,'end_line':end_line});walk(node.body,qual_stack,in_class=isinstance(node,ast_mod.ClassDef))
	walk(tree.body,[],in_class=False);return entries
HTML_VOID_TAGS=frozenset({'area','base','br','col','embed','hr','img','input','link','meta','param','source','track','wbr'})
HTML_LANDMARK_TAGS=frozenset({'main','header','footer','nav','aside','section','article','dialog'})
HTML_FORM_TAGS=frozenset({'form','fieldset','legend','input','textarea','select','output','label'})
HTML_INTERACTIVE_TAGS=frozenset({'button','details','summary'})
HTML_STRUCTURAL_TAGS=frozenset({'html','head','body','template','table','h1','h2','h3','h4','h5','h6','script','style','link','meta','title'})
HTML_KEY_ATTRIBUTES='id','class','role','type','name','href','src','for','action','method','aria-label','aria-labelledby','aria-controls','data-tab','data-action'
def _outline_html(rel_path,text):
	from html.parser import HTMLParser;del rel_path;entries=[];stack=[]
	def classify_tag(tag,attrs_map):
		if tag in HTML_LANDMARK_TAGS or'role'in attrs_map:return'landmark'
		if tag in HTML_FORM_TAGS:return'form'
		if tag in HTML_INTERACTIVE_TAGS or tag=='a'and'href'in attrs_map:return'interactive'
		if tag in HTML_STRUCTURAL_TAGS:return'structural'
		if any(key in attrs_map for key in('id','data-tab','data-action','aria-label','aria-labelledby','name')):return'identified'
		return None
	def build_label(tag,attrs_map):
		label=tag
		if attrs_map.get('id'):label+=f"#{attrs_map['id']}"
		if attrs_map.get('class'):
			classes='.'.join(part for part in attrs_map['class'].split()if part)
			if classes:label+=f".{classes}"
		elif not attrs_map.get('id')and attrs_map.get('name'):label+=f"[name={attrs_map['name']}]"
		return label
	class OutlineHTMLParser(HTMLParser):
		def _record_open(self,tag,attrs,self_closing=False):
			lower=tag.lower();attrs_map={}
			for(key,value)in attrs:
				if not key:continue
				k=key.lower()
				if k in HTML_KEY_ATTRIBUTES and value is not None and k not in attrs_map:attrs_map[k]=value
			tag_type=classify_tag(lower,attrs_map);line,_=self.getpos();raw=self.get_starttag_text()or'';end_line=line+raw.count('\n');entry=None
			if tag_type is not None:ordered_attrs={key:attrs_map[key]for key in HTML_KEY_ATTRIBUTES if key in attrs_map};summary=' '.join(f'{key}="{ordered_attrs[key]}"'for key in ordered_attrs);entry={'type':tag_type,'tag':lower,'label':build_label(lower,ordered_attrs),'attributes':ordered_attrs,'summary':summary,'line':line,'end_line':end_line};entries.append(entry)
			if not self_closing and lower not in HTML_VOID_TAGS:stack.append((lower,entry))
		def handle_starttag(self,tag,attrs):self._record_open(tag,attrs,self_closing=False)
		def handle_startendtag(self,tag,attrs):self._record_open(tag,attrs,self_closing=True)
		def handle_endtag(self,tag):
			lower=tag.lower();end_line,_=self.getpos()
			for index in range(len(stack)-1,-1,-1):
				open_tag,_open_entry=stack[index]
				if open_tag==lower:
					for(_,closed_entry)in stack[index:]:
						if closed_entry is not None and end_line>closed_entry['end_line']:closed_entry['end_line']=end_line
					del stack[index:];break
	parser=OutlineHTMLParser(convert_charrefs=True);parser.feed(text);parser.close();return entries
JS_OUTLINE_SCRIPT='\nconst fs = require("node:fs");\nconst acornPath = process.argv[1];\nconst suffix = process.argv[2];\nconst source = fs.readFileSync(0, "utf8");\nconst acorn = require(acornPath);\n\nfunction parseSource(text, ext) {\n  const modes = ext === ".cjs" ? ["script", "module"] : ext === ".mjs" ? ["module"] : ["module", "script"];\n  let firstErr = null;\n  for (const sourceType of modes) {\n    try {\n      return acorn.parse(text, {\n        ecmaVersion: "latest",\n        sourceType,\n        locations: true,\n        allowHashBang: true,\n      });\n    } catch (err) {\n      if (!firstErr) firstErr = err;\n    }\n  }\n  throw firstErr;\n}\n\nlet tree;\ntry {\n  tree = parseSource(source, suffix);\n} catch (err) {\n  const line = (err && err.loc && err.loc.line) || 1;\n  const col = err && err.loc && typeof err.loc.column === "number" ? err.loc.column + 1 : 1;\n  const msg = String((err && err.message) || "SyntaxError").replace(/\\s*\\(\\d+:\\d+\\)\\s*$/, "");\n  console.log(JSON.stringify({ error: { line, column: col, message: msg } }));\n  process.exit(0);\n}\n\nfunction sliceNode(node) {\n  if (!node || typeof node.start !== "number" || typeof node.end !== "number") return "";\n  return source.slice(node.start, node.end).replace(/\\s+/g, " ").trim();\n}\n\nfunction formatParams(params) {\n  return (params || []).map(param => sliceNode(param)).join(", ");\n}\n\nfunction propLabel(key, computed) {\n  if (!key) return "<anonymous>";\n  if (computed) return `[${sliceNode(key)}]`;\n  if (key.type === "Identifier") return key.name;\n  if (key.type === "PrivateIdentifier") return `#${key.name}`;\n  if (key.type === "Literal") return String(key.value);\n  return sliceNode(key);\n}\n\nconst entries = [];\n\nfunction addEntry(type, name, stack, signature, node, defNode) {\n  const qual = [...stack, name].join(".");\n  const spanNode = node || defNode;\n  const dNode = defNode || spanNode;\n  entries.push({\n    type,\n    name,\n    qualified_name: qual,\n    signature,\n    line: spanNode.loc.start.line,\n    def_line: dNode.loc.start.line,\n    end_line: spanNode.loc.end.line,\n  });\n  return [...stack, name];\n}\n\nfunction walkClass(classNode, className, stack, outerNode, exportPrefix) {\n  const superText = classNode.superClass ? ` extends ${sliceNode(classNode.superClass)}` : "";\n  const prefix = exportPrefix ? `${exportPrefix} ` : "";\n  const classStack = addEntry(\n    "class",\n    className,\n    stack,\n    `${prefix}class ${className}${superText}`,\n    outerNode || classNode,\n    classNode\n  );\n  const body = (classNode.body && classNode.body.body) || [];\n  for (const member of body) {\n    if (!member) continue;\n    if (member.type === "MethodDefinition") {\n      const mName = propLabel(member.key, member.computed);\n      const fn = member.value;\n      const isAsync = Boolean(fn && fn.async);\n      const isGen = Boolean(fn && fn.generator);\n      const parts = [];\n      const memberRole = member["k" + "ind"];\n      if (member.static) parts.push("static");\n      if (memberRole === "get" || memberRole === "set") parts.push(memberRole);\n      if (isAsync) parts.push("async");\n      const star = isGen ? "*" : "";\n      const prefixStr = parts.length ? `${parts.join(" ")} ` : "";\n      const sig = `${prefixStr}${star}${mName}(${formatParams(fn && fn.params)})`;\n      const methodStack = addEntry(\n        isAsync ? "async_method" : "method",\n        mName,\n        classStack,\n        sig,\n        member,\n        member\n      );\n      if (fn && fn.body) walkStatements(fn.body.body || [], methodStack, false);\n    } else if (member.type === "PropertyDefinition" && member.value) {\n      const pName = propLabel(member.key, member.computed);\n      if (\n        member.value.type === "ArrowFunctionExpression" ||\n        member.value.type === "FunctionExpression"\n      ) {\n        const fn = member.value;\n        const isAsync = Boolean(fn.async);\n        const parts = [];\n        if (member.static) parts.push("static");\n        if (isAsync) parts.push("async");\n        const prefixStr = parts.length ? `${parts.join(" ")} ` : "";\n        const sig = `${prefixStr}${pName}(${formatParams(fn.params)})`;\n        const methodStack = addEntry(\n          isAsync ? "async_method" : "method",\n          pName,\n          classStack,\n          sig,\n          member,\n          member\n        );\n        if (fn.body && fn.body.type === "BlockStatement") {\n          walkStatements(fn.body.body || [], methodStack, false);\n        }\n      }\n    }\n  }\n}\n\nfunction walkFunction(fnNode, fnName, stack, outerNode, exportPrefix) {\n  const isAsync = Boolean(fnNode.async);\n  const isGen = Boolean(fnNode.generator);\n  const prefix = exportPrefix ? `${exportPrefix} ` : "";\n  const asyncStr = isAsync ? "async " : "";\n  const fnKeyword = fnNode.type === "ArrowFunctionExpression" ? "" : `function${isGen ? "*" : ""} `;\n  const arrowSuffix = fnNode.type === "ArrowFunctionExpression" ? " =>" : "";\n  const sig = `${prefix}${asyncStr}${fnKeyword}${fnName}(${formatParams(fnNode.params)})${arrowSuffix}`;\n  const fnStack = addEntry(\n    isAsync ? "async_function" : "function",\n    fnName,\n    stack,\n    sig,\n    outerNode || fnNode,\n    fnNode\n  );\n  if (fnNode.body && fnNode.body.type === "BlockStatement") {\n    walkStatements(fnNode.body.body || [], fnStack, false);\n  }\n}\n\nfunction walkVariableDeclaration(decl, stack, topLevel, outerNode, exportPrefix) {\n  const declType = decl["k" + "ind"] || "var";\n  const prefix = exportPrefix ? `${exportPrefix} ` : "";\n  for (const declarator of decl.declarations || []) {\n    if (!declarator || !declarator.id || declarator.id.type !== "Identifier") continue;\n    const name = declarator.id.name;\n    const init = declarator.init;\n    const spanNode = (decl.declarations.length === 1 && outerNode) || declarator;\n    if (init && (init.type === "ArrowFunctionExpression" || init.type === "FunctionExpression")) {\n      const isAsync = Boolean(init.async);\n      const isGen = Boolean(init.generator);\n      const asyncStr = isAsync ? "async " : "";\n      const rhs =\n        init.type === "ArrowFunctionExpression"\n          ? `${asyncStr}(${formatParams(init.params)}) =>`\n          : `${asyncStr}function${isGen ? "*" : ""}(${formatParams(init.params)})`;\n      const fnStack = addEntry(\n        isAsync ? "async_function" : "function",\n        name,\n        stack,\n        `${prefix}${declType} ${name} = ${rhs}`,\n        spanNode,\n        declarator\n      );\n      if (init.body && init.body.type === "BlockStatement") {\n        walkStatements(init.body.body || [], fnStack, false);\n      }\n    } else if (init && init.type === "ClassExpression") {\n      walkClass(init, name, stack, spanNode, `${prefix}${declType}`);\n    } else if (topLevel) {\n      addEntry("binding", name, stack, `${prefix}${declType} ${name}`, spanNode, declarator);\n    }\n  }\n}\n\nfunction walkStatements(statements, stack, topLevel) {\n  for (const stmt of statements || []) {\n    if (!stmt) continue;\n    if (stmt.type === "FunctionDeclaration" && stmt.id) {\n      walkFunction(stmt, stmt.id.name, stack, stmt, "");\n    } else if (stmt.type === "ClassDeclaration" && stmt.id) {\n      walkClass(stmt, stmt.id.name, stack, stmt, "");\n    } else if (stmt.type === "VariableDeclaration") {\n      walkVariableDeclaration(stmt, stack, topLevel, stmt, "");\n    } else if (stmt.type === "ExportNamedDeclaration") {\n      if (stmt.declaration) {\n        const d = stmt.declaration;\n        if (d.type === "FunctionDeclaration" && d.id) {\n          walkFunction(d, d.id.name, stack, stmt, "export");\n        } else if (d.type === "ClassDeclaration" && d.id) {\n          walkClass(d, d.id.name, stack, stmt, "export");\n        } else if (d.type === "VariableDeclaration") {\n          walkVariableDeclaration(d, stack, topLevel, stmt, "export");\n        }\n      }\n    } else if (stmt.type === "ExportDefaultDeclaration") {\n      const d = stmt.declaration;\n      if (d && (d.type === "FunctionDeclaration" || d.type === "FunctionExpression")) {\n        const name = (d.id && d.id.name) || "default";\n        walkFunction(d, name, stack, stmt, "export default");\n      } else if (d && (d.type === "ClassDeclaration" || d.type === "ClassExpression")) {\n        const name = (d.id && d.id.name) || "default";\n        walkClass(d, name, stack, stmt, "export default");\n      }\n    }\n  }\n}\n\nwalkStatements(tree.body || [], [], true);\nconsole.log(JSON.stringify({ entries }));\n'
def _outline_javascript(rel_path,text,suffix,repo_root=None,parser_dir=None,which=shutil.which,runner=subprocess.run):
	status=discover_portable_parsers(parser_dir);acorn_entry=status['acorn']
	if acorn_entry is None and parser_dir is None:
		for candidate in(_outline_repo_root(repo_root),Path.cwd().resolve(),*Path(__file__).resolve().parents):
			acorn_entry,_=_check_parser_package(candidate,'acorn',PARSER_PACKAGES['acorn'])
			if acorn_entry is not None:break
	node_bin=which('node')
	if not node_bin or not acorn_entry:raise ValueError(f"{rel_path}: JavaScript outline unavailable: {status['message']}")
	completed=runner([node_bin,'-e',JS_OUTLINE_SCRIPT,acorn_entry,suffix],input=text,capture_output=True,text=True,check=False,timeout=15)
	if completed.returncode!=0:detail=(completed.stderr or completed.stdout or'acorn runner failed').strip();raise ValueError(f"{rel_path}: JavaScript outline failed: {detail}")
	try:payload=json.loads(completed.stdout)
	except json.JSONDecodeError as error:raise ValueError(f"{rel_path}: invalid JavaScript outline payload: {error}")from None
	if'error'in payload:err=payload['error'];raise ValueError(f"{rel_path}:{err['line']}:{err['column']}: JavaScript syntax error: {err['message']}")
	return payload['entries']
def outline_file(path,repo_root=None,max_entries=OUTLINE_MAX_ENTRIES,parser_dir=None):
	if type(max_entries)is not int or not 1<=max_entries<=OUTLINE_MAX_ENTRIES:raise ValueError(f"--max-entries must be an integer from 1 to {OUTLINE_MAX_ENTRIES}")
	rel_path,resolved,text=_resolve_outline_source(path,repo_root=repo_root);suffix=resolved.suffix.lower()
	if suffix=='.py':language='python';all_entries=_outline_python(rel_path,text)
	elif suffix in{'.html','.htm'}:language='html';all_entries=_outline_html(rel_path,text)
	elif suffix in{'.js','.mjs','.cjs'}:language='javascript';all_entries=_outline_javascript(rel_path,text,suffix,repo_root=repo_root,parser_dir=parser_dir)
	else:raise ValueError(f"{rel_path}: unsupported outline file type ({suffix or'no extension'})")
	entries=all_entries[:max_entries];total=len(all_entries);return{'path':rel_path,'language':language,'entries':entries,'total':total,'omitted':max(0,total-len(entries))}
def format_outline(result,as_json=False):
	if as_json:return json.dumps(result,ensure_ascii=False)
	entries=result['entries'];total=result['total'];omitted=result['omitted'];unit='tags'if result['language']=='html'else'symbols';omitted_note=f", {omitted} omitted"if omitted else'';lines=[f"{result['path']} ({result['language']}, {len(entries)}/{total} {unit}{omitted_note})"]
	for entry in entries:
		span=f"{entry['line']}-{entry['end_line']}";label=entry.get('qualified_name')or entry.get('label')or entry.get('name','');detail=entry.get('signature')or entry.get('summary')or''
		if detail:lines.append(f"{span} {label} — {detail}")
		else:lines.append(f"{span} {label}")
	if omitted:singular=unit[:-1]if omitted==1 else unit;lines.append(f"... {omitted} more {singular} omitted (use --max-entries up to {OUTLINE_MAX_ENTRIES})")
	return'\n'.join(lines)
IMAGE_IN_MARKDOWN=re.compile('!\\[([^\\]\\n]*)\\]\\(\\s*(<[^>]+>|[^)\\s]+)([^)\\n]*)\\)')
def resolve_report_image(path_arg,root=None):
	clean=str(path_arg or'').strip()
	if not clean:raise ValueError('Report image path is required')
	if WIREFRAME_IMAGE.search(clean):raise ValueError(f"Wireframe image {clean} (*-wireframe.*) is for agent inspection only and cannot be served in reports")
	start=Path(root).resolve()if root is not None else _outline_repo_root();base=next((parent for parent in(start,*start.parents)if(parent/'.git').exists()),start);raw_path=Path(clean);candidate=raw_path.resolve()if raw_path.is_absolute()else(base/raw_path).resolve()
	if not candidate.is_file()and not raw_path.is_absolute():
		for search_dir in(start,base/'arena-state'/'reports'):
			alt=(search_dir/raw_path).resolve()
			if alt.is_relative_to(search_dir)and alt.is_file():base=search_dir;candidate=alt;break
	if not candidate.is_relative_to(base):raise ValueError(f"Report image {clean} is outside the repository root")
	mime,encoded=image_payload(candidate);return mime,base64.b64decode(encoded),encoded
def inline_report_image_urls(markdown,report_dir=None):
	from urllib.parse import parse_qsl;root=_outline_repo_root(report_dir)
	def replace(match):
		alt,destination,suffix=match.groups();source=destination[1:-1]if destination.startswith('<')else destination
		if not source.startswith('/api/report-image?'):return match.group(0)
		query=dict(parse_qsl(urlsplit(source).query));rel_target=query.get('path','')
		try:mime,_,encoded=resolve_report_image(rel_target,root)
		except ValueError:return match.group(0)
		return f"![{alt}](data:{mime};base64,{encoded}{suffix})"
	return IMAGE_IN_MARKDOWN.sub(replace,markdown)
def embed_report_images(markdown,report_dir):
	from urllib.parse import parse_qsl
	def swap(match):
		alt,destination,suffix=match.groups();source=destination[1:-1]if destination.startswith('<')else destination
		if source.startswith('data:'):return match.group(0)
		if source.startswith('/api/report-image?'):query=dict(parse_qsl(urlsplit(source).query));resolve_report_image(query.get('path',''),_outline_repo_root(report_dir));return match.group(0)
		if source.startswith('//')or re.match('[a-zA-Z][a-zA-Z0-9+.-]*:',source):return f"Publication skips remote image {source}. Fetch it locally, then embed it with arena-preview image-data-uri."
		path=Path(source)
		if not path.is_absolute():path=report_dir/path
		if WIREFRAME_IMAGE.search(path.name)or WIREFRAME_IMAGE.search(source):raise ValueError(f"Wireframe image {source} (*-wireframe.*) is for agent inspection only and cannot be embedded in reports")
		try:mime,encoded=image_payload(path)
		except ValueError as error:raise ValueError(f"Publication skips local image {source}: {error}. Run arena-preview image-data-uri for the helper.")from error
		return f"![{alt}](data:{mime};base64,{encoded}{suffix})"
	return IMAGE_IN_MARKDOWN.sub(swap,markdown)
def check_report_nemoizer(source,markdown,repo_root=None):
	if repo_root is None:return
	nemoize_py=Path(repo_root).resolve()/'.agents'/'skills'/'nemoizer'/'scripts'/'nemoize.py'
	if not nemoize_py.is_file():return
	lines=[];after_final_clause=False
	for line in markdown.splitlines():
		stripped=line.strip()
		if re.match('^Final clause\\b',stripped,re.IGNORECASE):after_final_clause=True;lines.append(line);continue
		if after_final_clause and stripped:
			if stripped.startswith('"')and stripped.endswith('"'):lines.append('"Quoted clause."');after_final_clause=False;continue
			after_final_clause=False
		masked=re.sub('^(\\|\\s*\\d+(?:-\\d+)?\\s*\\|)\\s*.*?\\s*(\\|[^|]+\\|\\s*)$','\\1 ok \\2',line);lines.append(masked)
	with tempfile.NamedTemporaryFile(mode='w',suffix='.md',encoding='utf-8',delete=False)as handle:handle.write('\n'.join(lines)+'\n');tmp_name=handle.name
	try:proc=subprocess.run([sys.executable,str(nemoize_py),'--no-config','--context','description',tmp_name],capture_output=True,text=True,timeout=25,check=False)
	finally:Path(tmp_name).unlink(missing_ok=True)
	if proc.returncode!=0:detail=(proc.stdout or proc.stderr or'').replace(tmp_name,str(source)).strip();raise ValueError(f"nemoizer check failed for {source}:\n{detail}")
def gate_verdict(state_dir,push,line,out=None):
	out=sys.stdout if out is None else out;run_notices(state_dir,out=out)
	if poll_timeout_line(line):print('TIMEOUT BANNED: never wrap `arena-preview poll` in shell `timeout`. Set the Bash tool timeout to 1800 seconds; the poll returns within its 1799-second wait.',file=out,flush=True);return 1
	try:
		gate_store=Store(state_dir)
		if not push:
			for notice in gate_store.take_board_notifications():print(notice,file=out,flush=True)
		allowed=gate_store.gate(pending_only=push);blocked_calls=gate_store.calls_since_message()if not allowed and not push else None
	except FileNotFoundError:allowed=True;blocked_calls=None
	except Exception:return 2
	if not allowed:
		if blocked_calls is not None:print(f"BASH BLOCKED AFTER {blocked_calls} CALLS.",file=out,flush=True)
		if push:print('PUSH BLOCKED: a note or answer awaits an ack, so nothing left the sandbox. Read the inbox, ack every item, push again.',file=out,flush=True)
		print('READ INBOX NOW. Only a bare `arena-preview read` passes. Then ack every note: `arena-preview ack <id> <markdown>`.',file=out,flush=True)
		try:waiting=[item['id']for item in gate_store.read()['pending']]
		except Exception:waiting=[]
		if waiting:print('Pending: '+', '.join(waiting),file=out,flush=True)
		hint=gate_line_hint(line)
		if hint:print(hint,file=out,flush=True)
		blocker=first_blocking_piece(line)
		if blocker:blocker=shell_command_text(blocker);print(f"Blocked by the part `{blocker}`. Run the inbox calls alone, or drop this part.",file=out,flush=True)
		print(REMINDERS[0],file=out,flush=True);return 1
	if push and main_identical():print('HEAD equals `origin/main`, so the push carries nothing. Start new work from `origin/main`.',file=out,flush=True);return 1
	stale=main_ancestor_warning()if push else''
	if stale:print(stale,file=out,flush=True);return 1
	board_gate=board_main_session_warning(state_dir,line)
	if board_gate:print(board_gate,file=out,flush=True);return 1
	return 0
PRE_STEPS='interrupt','read-set','remind','main-local','main-fresh','bare','proxy','gate'
GATED_SHELLS=frozenset({'bash','sh','dash','ksh','zsh'})
PLATFORM_LINES='arena-workspace','ss -ltn','netstat -ltn','source ~/.profile',"mkdir -p '/home/user'","/bin/bash -l -c mkdir -p '","/bin/bash -l -c [ -e '","python3 -I - <<'PY'"
def platform_line(line):return any(pattern in line for pattern in PLATFORM_LINES)
def state_file(state_dir):return Path(state_dir)/'state.sqlite3'
def bare_script_invocation(line):
	lexer=shlex.shlex(agent_line(line),posix=False,punctuation_chars=';&|\n');lexer.whitespace=' \t\r';lexer.whitespace_split=True
	try:tokens=list(lexer)
	except ValueError:return False
	piece=[]
	for token in[*tokens,';']:
		if token not in(';','&&','||','|','|&','&','\n'):piece.append(token);continue
		try:words=[''.join(shlex.split(word))for word in piece]
		except ValueError:return False
		piece=[]
		while words and(re.fullmatch('[A-Za-z_]\\w*=.*',words[0])or words[0]in SHELL_KEYWORDS):words.pop(0)
		while words and words[0].rsplit('/',1)[-1]in PR_CHECK_WRAPPERS:
			wrapper=words.pop(0).rsplit('/',1)[-1]
			if wrapper=='command'and words and re.fullmatch('-[pVv]*[Vv][pVv]*',words[0]):words=[];break
			while words and(words[0].startswith('-')or'='in words[0]or words[0].isdigit()):words.pop(0)
		if not words:continue
		head=words[0].rsplit('/',1)[-1]
		if head in SHELL_BASENAMES:
			for(index,word)in enumerate(words[1:-1],1):
				if word.startswith('-')and'c'in word:
					if bare_script_invocation(words[index+1]):return True
					break
		if head.startswith('python')or head in SHELL_BASENAMES:
			words.pop(0)
			while words and words[0].startswith('-')and words[0]not in('-c','-m'):words.pop(0)
		if words and words[0].endswith(('/scripts/arena-preview','/scripts/preview.py')):return True
	return False
def sleep_command(line):
	lexer=shlex.shlex(agent_line(line or''),posix=False,punctuation_chars=';&|\n');lexer.whitespace=' \t\r';lexer.whitespace_split=True
	try:tokens=list(lexer)
	except ValueError:return False
	piece=[]
	for token in[*tokens,';']:
		if token not in(';','&&','||','|','|&','&','\n'):piece.append(token);continue
		try:words=[''.join(shlex.split(word))for word in piece]
		except ValueError:return False
		piece=[]
		while words and(re.fullmatch('[A-Za-z_]\\w*=.*',words[0])or words[0]in SHELL_KEYWORDS):words.pop(0)
		while words and words[0].rsplit('/',1)[-1]in PR_CHECK_WRAPPERS:
			wrapper=words.pop(0).rsplit('/',1)[-1]
			if wrapper=='command'and words and re.fullmatch('-[pVv]*[Vv][pVv]*',words[0]):words=[];break
			while words and(words[0].startswith('-')or'='in words[0]or words[0].isdigit()):words.pop(0)
		if not words:continue
		head=words[0].rsplit('/',1)[-1]
		if head in SHELL_BASENAMES:
			for(index,word)in enumerate(words[1:-1],1):
				if word.startswith('-')and'c'in word:
					if sleep_command(words[index+1]):return True
					break
		if head=='sleep':return True
	return False
def pre_command(state_dir,line,command,root,done='',shell=''):
	initial=done.split();steps=list(initial);finished=set(steps);routed=os.environ.get('ARENA_PREVIEW_ROUTE')=='1'
	if not routed and state_file(state_dir).is_file():
		group_store=Store(state_dir,create=False)
		if not platform_line(line):group_store.set_meta(BASH_RUNNING_META,now())
		if sleep_command(command):
			group=str(os.getpgid(0))
			if group_store.meta_value(CALL_GROUP_META)!=group:group_store.set_meta(CALL_GROUP_META,group);group_store.set_meta(CALL_STARTED_META,now())
		elif group_store.meta_value(CALL_GROUP_META):group_store.set_meta(CALL_GROUP_META,'')
	def report(warning):print(warning,file=sys.stderr,flush=True)
	def mark(step):
		if step not in finished:finished.add(step);steps.append(step)
	def finish(status):
		if steps!=initial:done_line=' '.join(steps);print(f'_arena_preview_done="{done_line}"',flush=True)
		return status
	warning=pr_state_warning(command)
	if warning:report(warning);return finish(130)
	if shell and shell not in GATED_SHELLS:return finish(0)
	if platform_line(line):return finish(0)
	if not routed and'read-set'not in finished and state_file(state_dir).is_file():
		mark('read-set');tick_store=Store(state_dir)
		if preview_server_available(tick_store):read_set_tick(tick_store,line,moment=datetime.now(resolve_display_zone(tick_store)),root=root)
	if'commit-remind'not in finished and'git commit'in command:
		mark('commit-remind');gate_store=Store(state_dir)if state_file(state_dir).is_file()else None
		if is_private_repo(root,store=gate_store):report('Private repo: batch at most 3 verified commits, and never starve pushes.')
		else:report('ALWAYS push. NEVER delay for batch.')
	if'remind'not in finished and any(word in command for word in('git commit','git push','gh pr checks')):mark('remind');report('Task done? Run arena-preview task <id> --status finished.')
	if'git commit'in command and'main-local'not in finished:
		mark('main-local');warning=main_drift_warning(Path.cwd(),fresh=False)
		if warning:report(warning)
	elif'git push'in command and'main-fresh'not in finished:
		mark('main-fresh');warning=main_drift_warning(Path.cwd())
		if warning:report(warning)
	warning=inbox_pipeline_warning(line)or read_set_input_warning(line,root)
	if warning:report(warning);return finish(130)
	if routed:return finish(0)
	warning=idle_wait(line,state_dir=state_dir)
	if warning:report(warning);return finish(130)
	status=pr_check_wait(state_dir,line,command)
	if status==PR_CHECK_RUNS:return finish(2)
	if status==PR_CHECK_REFUSED:return finish(130)
	if'bare'not in finished and bare_script_invocation(line):mark('bare');report('Use bare arena-preview from any directory. Drop full path.')
	recipe_status=route_recipe_command(state_dir,line,root=root)
	if recipe_status is not None:return finish(recipe_status)
	if'proxy'not in finished and any(word in line for word in('code-scanning','code_scanning','gh run view','gh run download','actions/runs')):mark('proxy');report('Use proxy: code-scanning alerts, run logs. Routes: arena skill.')
	if'arena-preview ack'in command or'preview.py ack'in command:
		warning=tick_warning(command)
		if warning:report(warning)
	if'arena-preview serve'in command or'preview.py serve'in command:
		warning=serve_warning(command)
		if warning:report(warning)
	if any(word in command for word in('curl','wget','git clone','git fetch'))or'git ls-remote'in command:
		warning=egress_warning(command)
		if warning:report(warning)
	if'git add'in command and re.search('(?:^|[\\s\'\\"/])[^\\s\'\\"]*-wireframe\\.png(?:$|[\\s\'\\"])',command,re.IGNORECASE):report('WIREFRAME BLOCKED: never stage or commit wireframe captures (*-wireframe.png).');return finish(130)
	if'git commit'in command:
		warning=worktree_warning(state_dir,'commit',cwd=Path.cwd())
		if warning:report(warning);return finish(130)
		try:staged_wf=subprocess.run(['git','diff','--cached','--name-only','--diff-filter=ACMR'],cwd=str(Path.cwd()),capture_output=True,text=True,check=False);wf_paths=[line.strip()for line in staged_wf.stdout.splitlines()if line.strip().lower().endswith('-wireframe.png')]
		except OSError:wf_paths=[]
		if wf_paths:report('WIREFRAME BLOCKED: never stage or commit wireframe captures (*-wireframe.png): '+', '.join(wf_paths));return finish(130)
		try:commit_store=Store(state_dir)
		except FileNotFoundError:commit_store=None
		if commit_store is not None:
			found=commit_id_report(staged_session_id_hits(commit_store,str(Path.cwd())))
			if found:report(found);return finish(130)
	if any(word in command for word in('arena-workspace','ss -ltn','netstat -ltn')):return finish(0)
	if inert_bash_command(command):refresh_bash_timer(state_dir);return finish(0)
	if'git push'in command:
		warning=worktree_warning(state_dir,'push')
		if warning:report(warning);return finish(130)
		if gate_verdict(state_dir,True,line,out=sys.stderr)==1:return finish(130)
	if quiet_inbox_line(line):refresh_bash_timer(state_dir);return finish(0)
	if'gate'not in finished:
		mark('gate')
		if gate_verdict(state_dir,False,line,out=sys.stderr)==1:return finish(130)
	return finish(0)
def watch_call_group(store,stop=None,interval=1.,signal_group=os.killpg):
	seen=None
	while stop is None or not stop.is_set():
		flag=store.meta_value(AGENT_INTERRUPT_FLAG_META)
		if flag and flag!=seen:
			seen=flag
			try:reason=json.loads(flag).get('type','')
			except ValueError:reason=''
			age=seconds_since(store.meta_value(CALL_STARTED_META))
			if reason==CALL_STOP_TYPE and age is not None and age>CALL_STOP_SECONDS:
				try:group=int(store.meta_value(CALL_GROUP_META)or 0)
				except(TypeError,ValueError):group=0
				if group>1:
					try:signal_group(group,signal.SIGINT)
					except(ProcessLookupError,PermissionError):pass
					store.set_meta(CALL_GROUP_META,'')
		if stop is None:time.sleep(interval)
		elif stop.wait(interval):return
WORKSPACE_WATCH_SKIP_DIRS=frozenset({'.git','.agents','arena-state','node_modules','__pycache__','.pytest_cache','.ruff_cache','.mypy_cache','.venv','venv','dist','build','.tox','.nox','.cache'})
def workspace_file_snapshot(root):
	snapshot={};root_path=Path(root)
	if not root_path.is_dir():return snapshot
	state_dir_name=''
	for(dirpath,dirnames,filenames)in os.walk(root_path,followlinks=False):
		dirnames[:]=[name for name in dirnames if name not in WORKSPACE_WATCH_SKIP_DIRS and name!=state_dir_name and not name.startswith('.arena-worktree')]
		for fname in filenames:
			if fname.endswith(('.pyc','.swp','.tmp'))or fname.startswith('.#'):continue
			full=os.path.join(dirpath,fname)
			try:st=os.lstat(full)
			except OSError:continue
			if os.path.islink(full):continue
			rel=os.path.relpath(full,root_path);snapshot[rel]=st.st_mtime_ns,st.st_size
	return snapshot
def bash_call_running(store):
	bash_at=store.meta_value(BASH_RUNNING_META)
	if bash_at:
		age=seconds_since(bash_at)
		if age is not None and 0<=age<1800:return True
	try:group=int(store.meta_value(CALL_GROUP_META)or 0)
	except(TypeError,ValueError):group=0
	if group>1:
		age=seconds_since(store.meta_value(CALL_STARTED_META))
		if age is not None and 0<=age<1800:return True
	return False
def record_workspace_file_changes(store,previous,current):
	ended_dt=import_stamp(store.meta_value(AGENT_CALL_ENDED_META));ended_ns=int(ended_dt.astimezone(UTC).timestamp()*1000000000)+20000000 if ended_dt is not None else 0;created=False;edited=False
	for(rel,sig)in current.items():
		if sig[0]<=ended_ns:continue
		if rel not in previous:created=True;break
		if previous[rel]!=sig:edited=True
	if created:store.record_tool('write');return'write'
	if edited:store.record_tool('edit');return'edit'
	return None
def watch_workspace_files(store,stop=None,interval=1.):
	try:root=store.repository_root()
	except Exception:root=Path.cwd()
	previous=workspace_file_snapshot(root)
	while stop is None or not stop.is_set():
		if stop is None:time.sleep(interval)
		elif stop.wait(interval):return
		try:
			current=workspace_file_snapshot(root)
			if not bash_call_running(store):record_workspace_file_changes(store,previous,current)
			previous=current
		except Exception:pass
def ensure_workspace_watcher(store,stop=None,interval=1.):
	prev_stop=getattr(sys,'_arena_workspace_watcher_stop',None)
	if prev_stop is not None and prev_stop is not stop:prev_stop.set()
	watcher_stop=stop if stop is not None else threading.Event();sys._arena_workspace_watcher_stop=watcher_stop;threading.Thread(target=watch_workspace_files,args=(store,watcher_stop,interval),daemon=True).start();return watcher_stop
def watch_hot_reload(server,reload_event,stop=None,interval=1.,scripts_dir=None,store=None):
	baseline=scripts_signature(scripts_dir)
	while stop is None or not stop.is_set():
		if stop is None:time.sleep(interval)
		elif stop.wait(interval):return
		current=scripts_signature(scripts_dir)
		if current!=baseline:
			baseline=current
			if hasattr(server,'RequestHandlerClass')and store is not None:
				try:
					import importlib;import importlib.util
					if'survey'in sys.modules:importlib.reload(sys.modules['survey'])
					spec=importlib.util.spec_from_file_location('_preview_hot',Path(__file__).resolve())
					if spec is not None and spec.loader is not None:mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod);state_root=store.path.parent if getattr(store,'path',None)is not None and store.path.name=='state.sqlite3'else getattr(store,'path',Path('arena-state'));new_store=mod.Store(state_root,create=False);server.RequestHandlerClass=mod.handler(new_store,reload_event=reload_event);store.set_meta('hot_reload_at',now());print('Hot-reloaded preview handler in-process.',flush=True)
				except Exception as error:print(f"Hot-reload skipped: {error}",file=sys.stderr,flush=True)
				reload_event.set()
				if stop is None:time.sleep(WS_TICK_SECONDS*1.5)
				elif stop.wait(WS_TICK_SECONDS*1.5):reload_event.clear();return
				reload_event.clear()
			else:
				reload_event.set()
				if stop is None:time.sleep(WS_TICK_SECONDS*1.5)
				if hasattr(server,'shutdown'):server.shutdown()
				return
class PreviewArgumentParser(argparse.ArgumentParser):
	def print_usage(self,file=None):pass
	def exit(self,status=0,message=None):super().exit(status,message.removeprefix('arena-preview: ')if message else message)
def main():
	parser=PreviewArgumentParser(description=CLI_DESCRIPTION,prog='arena-preview');parser.add_argument('-r','--reminder',action='store_true',help='Print unacked-count reminder; exit');parser.add_argument('-c','--rc',type=int,default=0,help='Exit status of shell call for --reminder');parser.add_argument('-l','--line',dest='reminder_line',default='',help='Shell command line for --reminder');parser.add_argument('-e','--elapsed',type=float,default=None,help=argparse.SUPPRESS);parser.add_argument('-P','--tool-probe',choices=TOOL_PROBE_NAMES,default=None,help=argparse.SUPPRESS);commands=parser.add_subparsers(dest='command',required=False);serve=commands.add_parser('serve',help='Serve preview page; -p names port');serve.add_argument('-p','--port',type=int,default=DEFAULT_PORT,help=f"Port (default: {DEFAULT_PORT}; only supported port)");init=commands.add_parser('init',help='Create state directory and database');init.add_argument('-z','--tz',help='CLI stamp IANA zone; TZ env wins');survey_parser=commands.add_parser('survey',help='Show questions and schema, or submit ordered JSON');survey_parser.add_argument('-i','--smallest-image',action='store_true',help='Print the smallest image path in the repository tree, or a temporary image path when none exists');survey_parser.add_argument('answers',nargs='?',help='Ordered answer JSON');commands.add_parser('read',help='Print every pending note, answer and upload');commands.add_parser('skills',help='List skill names, full descriptions, and paths without loading skills');agents_parser=commands.add_parser('agents',help='List AGENTS.md files from the repository root through the target directory');agents_parser.add_argument('path',nargs='?',default='.',type=Path,help='Target directory or file path (default: current directory)');note_group=commands.add_parser('note',help='Read stored notes');note_verbs=note_group.add_subparsers(dest='verb',required=True);note_list=note_verbs.add_parser('list',help='Stored note IDs by page, newest first; acknowledged included');note_list.add_argument('-p','--page',type=int,default=1,help='Page number (default: 1)');note_list.add_argument('-s','--page-size',type=int,default=20,help='IDs per page, 1 to 100 (default: 20)');note_reread=note_verbs.add_parser('show',help='Reread stored note by short or full ID');note_reread.add_argument('id',help='Six or all note ID characters');image_uri=commands.add_parser('image-data-uri',help='Print Markdown: one local image as data URI');image_uri.add_argument('path',type=Path,help='One local PNG, JPEG, GIF or WebP file up to 2 MiB');outline_parser=commands.add_parser('outline',help='Print read-only source outline with line ranges for local repository files');outline_parser.add_argument('path',type=Path,help='Local source file inside the repository checkout');outline_parser.add_argument('-m','--max-entries',type=int,default=OUTLINE_MAX_ENTRIES,help=f"Maximum entries to print, 1 to {OUTLINE_MAX_ENTRIES} (default: {OUTLINE_MAX_ENTRIES})");outline_parser.add_argument('-j','--json',dest='as_json',action='store_true',help='Print JSON outline with path, language, entries, total, and omitted count');pr_update_parser=commands.add_parser('pr-update',help='Update a pull request title and body via REST from a workspace file and verify parity');pr_update_parser.add_argument('-n','--number',type=int,required=True,help='Pull request number');pr_update_parser.add_argument('-t','--title',required=True,help='Pull request title');pr_update_parser.add_argument('-f','--body-file',type=Path,required=True,help='Workspace Markdown file for pull request body');job_log_parser=commands.add_parser('job-log',help='Resolve the signed GitHub Actions job-log Location URL or fallback /v1/gh proxy URL');job_log_parser.add_argument('-j','--job',dest='job_id',help='GitHub Actions job ID');job_log_parser.add_argument('-r','--run',dest='run_id',help='GitHub Actions run ID');commands.add_parser('key',help='Print the recorded agent key, host and use line');commands.add_parser('version',help='Print live egress proxy and boot userscript versions');pre=commands.add_parser('pre',help='Run all pre-Bash gates in order; list completed steps');pre.add_argument('-l','--line',default='',help='Shell command line from hook');pre.add_argument('-c','--command',dest='command_text',default='',help='Command reached by DEBUG trap');pre.add_argument('-r','--repo',default='.',help='Bible repository');pre.add_argument('-s','--shell',default='',help='Shell name');pre.add_argument('-d','--done',default='',help="This shell's completed steps, space-separated");run_group=commands.add_parser('run',help='Start long command; read result');run_verbs=run_group.add_subparsers(dest='verb',required=True);route=run_verbs.add_parser('start',help='Detach command, keep output tail; show exit on later call');route.add_argument('-t','--task',dest='task_id',metavar='TASK-ID',help='Link run to upcoming task, not current task');route.add_argument('-a','--after',metavar='GATE',help='Run only after this gate passes');route.add_argument('name',help='Short run name; example: gates');route.add_argument('command_args',nargs=argparse.REMAINDER,help='Command after --');route_run=commands.add_parser('route-run',help='Run command here; record exit for later call');route_run.add_argument('name',help='Run name already recorded by caller');route_run.add_argument('command_args',nargs=argparse.REMAINDER,help='Command after --');logs=run_verbs.add_parser('logs',help='Print routed run status and recorded output tail');logs.add_argument('name',help='Run name to read');worktree=commands.add_parser('worktree',help='Create or collect detached task worktree beside root gate');worktree_action=worktree.add_subparsers(dest='worktree_action',required=True);worktree_start=worktree_action.add_parser('start',help='Create detached worktree beside root gate, or without one');worktree_start.add_argument('name',help='Short worktree name');worktree_start.add_argument('destination',type=Path,help='New path outside root');worktree_start.add_argument('-g','--gate',default=None,help='Running root gate name; optional');worktree_start.add_argument('-a','--allow',action='append',default=[],help='Glob of paths task may change');worktree_collect=worktree_action.add_parser('collect',help='Print allowed detached-worktree patch after root gate passes');worktree_collect.add_argument('name',help='Worktree name to collect');worktree_forget=worktree_action.add_parser('forget',help='Remove a stored worktree record');worktree_forget.add_argument('name',help='Worktree name to forget');rename=commands.add_parser('rename',help='Move note, answer, report, or task to new ID');rename.add_argument('old_id',help='Stored ID or unique record prefix');rename.add_argument('new_id',help='Destination record ID');gate=commands.add_parser('gate',help='Inbox gate; -p blocks on any unacked note; -l names blocked line');gate.add_argument('-p','--push',action='store_true',help='Block on any unacked note or answer, regardless of call count');gate.add_argument('-l','--line',default='',help='Command line from hook; blocked calls name commands to drop');commands.add_parser('poll',help='Wait for owner answer or gate completion');download=commands.add_parser('download-request',help='Request HTTPS browser download; preview Approve click required; -a allows proxy fallback');download.add_argument('url',help='One HTTPS URL without embedded credentials');download.add_argument('-a','--allow-proxy',action='store_true',help='Owner may opt in to AllOrigins and CodeTabs fallback for this request');ack=commands.add_parser('ack',help='Answer notes with inline Markdown; omit IDs for one pending item');ack.add_argument('ids',nargs='*');ack.add_argument('markdown',help='Literal inline Markdown answer');ack.add_argument('-i','--id',dest='task_id');ack.add_argument('-t','--title',dest='task_title');ack.add_argument('-T','--type',dest='task_type',choices=TASK_TYPES);ack.add_argument('-S','--scope',dest='task_scope');ack.add_argument('-d','--detail',dest='task_detail',action='append');report_group=commands.add_parser('report',help='Publish, withdraw and inspect reports');report_verbs=report_group.add_subparsers(dest='verb',required=True);publish=report_verbs.add_parser('publish',help='Publish report; -i ID, -t title');publish.add_argument('source',type=Path);publish.add_argument('-s','--submission-format',type=Path,help='Optional question, answer, and template JSON');publish.add_argument('-i','--id',required=True);publish.add_argument('-t','--title',required=True);publish.add_argument('-u','--allow-unparsed-markers',action='store_true',help='Publish even when possible unparsed == or ~~ markers are found');unpublish=report_verbs.add_parser('unpublish',help='Withdraw report by ID, or all stale reports with --stale');unpublish.add_argument('report_id',nargs='?');unpublish.add_argument('-s','--stale',action='store_true',help='Withdraw every stale report');report_verbs.add_parser('stale',help='List stale published report IDs and reasons without changing state')
	for verb in('pin','unpin'):pin=report_verbs.add_parser(verb,help=f"{verb.capitalize()} a report against staleness");pin.add_argument('report_id')
	report_state=report_verbs.add_parser('state',help='Print publication, owner view stamp and answer visibility for one report or all');report_state.add_argument('report_id',nargs='?');report_answer=report_verbs.add_parser('answer',help='Answer a worker-published board report directly from main');report_answer.add_argument('worker',help='Worker session branch or @role alias');report_answer.add_argument('report_id',help='Worker report ID');report_answer.add_argument('answers_json',help='JSON object keyed by field ID');report_forward=report_verbs.add_parser('forward',help='Forward an owner-targeted worker board report unanswered to the owner');report_forward.add_argument('worker',help='Worker session branch or @role alias');report_forward.add_argument('report_id',help='Worker report ID');report_forward.add_argument('-i','--id',dest='local_id',default=None);report_forward.add_argument('-t','--title',dest='local_title',default=None);task_group=commands.add_parser('task',help='Read and update tasks');task_verbs=task_group.add_subparsers(dest='verb',required=True);task_list=task_verbs.add_parser('list',help='List tasks; -f includes finished');task_list.add_argument('-f','--full',action='store_true',help='print every task, finished included');task_show=task_verbs.add_parser('show',help='Print one stored task in full');task_show.add_argument('task_id',metavar='TASK-ID');task=task_verbs.add_parser('set',help='Write/update task; -s status, -o order, -d details, -A append, -m message, -r report, -u unblocked, -a amend, -t/--type type, -S/--scope scope');task.add_argument('id_arg',nargs='?',metavar='TASK-ID');task.add_argument('title_arg',nargs='?',metavar='TASK-TITLE');task.add_argument('detail_arg',nargs='*',metavar='TASK-DETAIL');task.add_argument('-d','--details',dest='task_details',action='append',help='One step per line; max 120 characters; repeatable');task.add_argument('-A','--append',dest='append_details',action='store_true',help='Append provided details instead of replacing stored details');task.add_argument('-u','--unblocked',dest='blocked',action='store_false',default=None,help='Clear report wait or legacy blocked mark');task.add_argument('-m','--msg-id',help='Message task answers; mark it as having a task');task.add_argument('-r','--report',metavar='REPORT-ID',help='Awaited report; answer clears blocked mark');task.add_argument('-a','--amend',metavar='PREV-ID',help='Rename task under this ID to given ID');task.add_argument('-b','--blocked-by',dest='blocked_by',action='append',metavar='TASK-ID',help='Dependency task; repeatable; cycles refused');task.add_argument('-s','--status',choices=TASK_STATUSES,default=None);task.add_argument('-t','-T','--type',dest='task_type',choices=TASK_TYPES,default=None,help='Primary deliverable type; omitted flag keeps stored type');task.add_argument('-S','--scope',dest='task_scope',default=None,help='Free-text task scope; omitted flag keeps stored scope');task.add_argument('-o','--order',type=int,default=None,help='1-based place within div, not end');task_unlink=task_verbs.add_parser('unlink',help='Unlink a submission from all tasks; retain its history');task_unlink.add_argument('submission_id');task_remove=task_verbs.add_parser('remove',help='Delete task by ID');task_remove.add_argument('task_id');state_group=commands.add_parser('state',help='Import, compare, save, restore, or clear stored state');state_verbs=state_group.add_subparsers(dest='verb',required=True);state_import=state_verbs.add_parser('import',help='Import NDJSON backup; replace live state');state_import.add_argument('source',nargs='?',type=Path);state_import.add_argument('-r','--replace-tasks',action='store_true');state_compare=state_verbs.add_parser('compare',help='Compare NDJSON backup against live state without changing state');state_compare.add_argument('source',nargs='?',type=Path);state_verbs.add_parser('save',help='Snapshot live state to state-latest.ndjson and queue userscript local sync');state_restore=state_verbs.add_parser('restore',help='Restore state-latest.ndjson or state-previous.ndjson and queue userscript local sync');state_restore.add_argument('-p','--previous',action='store_true',help='Restore state-previous.ndjson instead of state-latest.ndjson');state_verbs.add_parser('clear',help='Empty all state tables in place; keep agent key record');state_verbs.add_parser('status',help='Print arena-state file and byte usage by category');state_clean=state_verbs.add_parser('clean',help='Prune unreferenced uploads, downloads, finished runs, and scratch files');state_clean.add_argument('-n','--dry-run',action='store_true',help='List files that would be removed without deleting them');board_group=commands.add_parser('board',help='Inspect, post to, or wake sessions on the multi-session board');board_verbs=board_group.add_subparsers(dest='verb',required=True);board_verbs.add_parser('status',help='Show board sessions and pending entries');board_post_cmd=board_verbs.add_parser('post',help='Post a message or attachment to the multi-session board');board_post_cmd.add_argument('-t','--to',default=None,help="Target session ID, worker ID, or 'main'");board_post_cmd.add_argument('-f','--file',type=Path,default=None,help='Optional file attachment');board_post_cmd.add_argument('text',nargs='?',default='',help='Board post message text');board_wake_cmd=board_verbs.add_parser('wake',help='Wake a target session or all polling sessions');board_wake_cmd.add_argument('session',nargs='?',default='all-polling',help="Target session ID, worker ID, or 'all-polling' (default)");args=parser.parse_args()
	if args.command=='serve'and args.port!=DEFAULT_PORT:serve.error(f"--port must be {DEFAULT_PORT}")
	verb=getattr(args,'verb',None);grouped_commands={('note','list'):'note-list',('note','show'):'note',('run','start'):'route',('run','logs'):'logs',('report','publish'):'publish',('report','unpublish'):'unpublish',('report','stale'):'stale-reports',('report','state'):'report-state',('report','pin'):'report-pin',('report','unpin'):'report-unpin',('report','answer'):'report-answer',('report','forward'):'report-forward',('task','list'):'task-list',('task','show'):'task-show',('task','set'):'task',('task','remove'):'task-remove',('task','unlink'):'task-unlink',('state','import'):'import-state',('state','compare'):'compare-state',('state','save'):'save-state',('state','restore'):'restore-state',('state','clear'):'clear-state',('state','status'):'status-state',('state','clean'):'clean-state',('board','status'):'board-status',('board','post'):'board-post',('board','wake'):'board-wake'};args.command=grouped_commands.get((args.command,verb),args.command)
	if args.command=='route':
		command_args=args.command_args;separator=command_args.index('--')if'--'in command_args else len(command_args)
		if any(argument==option or argument.startswith(option+'=')for argument in command_args[:separator]for option in('-t','--task','-a','--after')):parser.error('put --task and --after before the run name. Use: arena-preview run start --task <id> <name> -- <command>')
	if args.command=='unpublish'and args.stale:
		if args.report_id:parser.error('give a report ID or --stale, not both')
		args.command='report'
	state_dir=resolve_state_dir();fresh_state=not(Path(state_dir).resolve()/'state.sqlite3').is_file()
	try:
		if args.reminder:
			store=Store(state_dir,create=False);require_server(store);auto_start_post_push_pr_checks(state_dir,args.reminder_line,rc=args.rc);slow_hint=slow_bash_hint(args.reminder_line,args.elapsed);reminder=store.reminder(advance=not exempted_bash_line(args.reminder_line));store.set_meta(AGENT_CALL_ENDED_META,now());store.clear_meta(BASH_RUNNING_META)
			if slow_hint:print(slow_hint,file=sys.stderr,flush=True)
			if'unacked 'in reminder or TASK_ORDER_NOTICE in reminder or not run_notices(state_dir,show_running=True):print(reminder,file=sys.stderr,flush=True)
			return 0
		if args.tool_probe:return tool_probe_command(state_dir,args.tool_probe)
		if not args.command:parser.error('Command required. Run arena-preview --help.')
		if args.command=='pre':return pre_command(state_dir,args.line,args.command_text,args.repo,done=args.done,shell=args.shell)
		if args.command=='survey':return survey_command(state_dir,args.answers,smallest_image=args.smallest_image)
		if args.command=='skills':print_skills();return 0
		if args.command=='agents':
			try:
				base,found=resolve_agents_chain(args.path)
				for item in found:print(item.relative_to(base).as_posix())
			except ValueError as error:print(error,file=sys.stderr);return 1
			return 0
		if args.command=='image-data-uri':
			try:print(image_data_uri(args.path))
			except ValueError as error:print(error,file=sys.stderr);return 1
			return 0
		if args.command=='outline':
			try:result=outline_file(args.path,max_entries=args.max_entries);print(format_outline(result,as_json=args.as_json))
			except ValueError as error:print(error,file=sys.stderr);return 1
			return 0
		if args.command=='pr-update':
			try:store=Store(state_dir,create=False)if state_file(state_dir).is_file()else None;print(pr_update_command(args.number,args.title,args.body_file,store=store))
			except ValueError as error:print(error,file=sys.stderr);return 1
			return 0
		if args.command=='job-log':
			try:store=Store(state_dir,create=False)if state_file(state_dir).is_file()else None;print(job_log_command(store,job_id=args.job_id,run_id=args.run_id))
			except ValueError as error:print(error,file=sys.stderr);return 1
			return 0
		if args.command=='worktree':
			if args.worktree_action=='start':record=start_worktree(state_dir,args.name,args.destination,args.gate,args.allow);print(f"Worktree {record['name']} at {record['worktree']}");return 0
			if args.worktree_action=='forget':removed=forget_worktree(state_dir,args.name);print(f"Forgot worktree {removed}");return 0
			print(collect_worktree(state_dir,args.name),end='');return 0
		if args.command=='route':
			warning=worktree_warning(state_dir,'gate')
			if warning:print(warning,file=sys.stderr);return 1
			return route_command(state_dir,args.name,args.command_args,task_id=args.task_id,after=args.after)
		if args.command=='route-run':
			warning=worktree_warning(state_dir,'gate')
			if warning:print(warning,file=sys.stderr);return 1
			return run_command(state_dir,args.name,args.command_args)
		if args.command=='logs':return run_log_report(state_dir,args.name)
		if args.command=='rename':
			require_server(Store(state_dir))
			try:old_id=Store(state_dir).resolve_id(args.old_id)
			except FileNotFoundError:print(f"No note, answer, report or task holds the ID {args.old_id}",file=sys.stderr);return 1
			except ValueError as error:print(error,file=sys.stderr);return 1
			try:type=Store(state_dir).rename_entity(old_id,args.new_id)
			except ValueError as error:print(error,file=sys.stderr);return 1
			print(f"ok {type} {old_id} -> {args.new_id}");return 0
		if args.command=='gate':return gate_verdict(state_dir,args.push,args.line)
		store=Store(state_dir,create=args.command in{'serve','init','import-state','restore-state'});global DISPLAY_ZONE;DISPLAY_ZONE=resolve_display_zone(store)
		if args.command!='poll':clear_poll_repeat(store)
		if args.command not in{'serve','init'}:require_server(store)
		read_only=args.command in{'stale-reports','note-list','report-state'}
		if not read_only:
			routine=os.environ.get('ARENA_PREVIEW_SHELL_HOOK')!='1'or os.environ.get('ARENA_PREVIEW_ROUTE')=='1';reminder=store.reminder(routine=routine)
			if reminder:print(reminder,file=sys.stderr,flush=True)
		if args.command!='serve'and not read_only:store.touch_agent()
		if args.command=='serve':
			from http.server import ThreadingHTTPServer;require_renderer();reload_event=threading.Event();stop_event=threading.Event();ThreadingHTTPServer.allow_reuse_address=True
			with ThreadingHTTPServer(('0.0.0.0',args.port),handler(store,reload_event=reload_event))as server:server.daemon_threads=True;store.set_meta('port',str(server.server_port));threading.Thread(target=watch_call_group,args=(store,stop_event),daemon=True).start();ensure_workspace_watcher(store,stop_event);threading.Thread(target=watch_hot_reload,args=(server,reload_event,stop_event,1.,None,store),daemon=True).start();print(f"Preview on 0.0.0.0:{server.server_port}; state: {store.path}",flush=True);server.serve_forever();stop_event.set()
		elif args.command=='read':require_server(store);store.notice_expired_key();print_read(store);store.take_board_wake()
		elif args.command=='note-list':
			ids,total=store.list_note_ids(args.page,args.page_size);pages=max(1,(total+args.page_size-1)//args.page_size)
			if args.page>pages:print(f"Page {args.page} is past the last page ({pages}).",file=sys.stderr);return 1
			print(f"Notes page {args.page}/{pages} ({total} total; newest first)")
			for note_id in ids:print(note_id)
		elif args.command=='note':
			rows=store.notes_matching(args.id)
			if not rows:print(f"No note matches {args.id}.",file=sys.stderr);return 1
			for row in rows:
				head=f"note {row['id']} at {local_stamp(row['at'])}";head+=f" acked {local_stamp(row['acknowledged_at'])}"if row['acknowledged_at']else' unacked';print(head);print(row['text'])
				if row.get('patch_hint'):print(row['patch_hint'])
				print()
		elif args.command=='key':
			record=store.agent_key()
			if not record:print('No agent key recorded.',file=sys.stderr);return 1
			ver=store.userscript_version();egress_ver=store.egress_version();enriched=dict(record)
			if egress_ver:enriched['egress_version']=egress_ver
			if ver:enriched['userscript_version']=ver
			print(format_key(enriched))
		elif args.command=='version':print(f"egress {store.egress_version()or'unknown'}");print(f"userscript {store.userscript_version()or'unknown'}")
		elif args.command=='poll':
			require_server(store);require_survey(store);store.notice_expired_key();refusal=poll_repeat(store)
			if refusal:print(refusal,flush=True);return 2
			return poll_once(store)
		elif args.command=='download-request':print(format_fetch_job(store.enqueue_fetch(args.url,args.allow_proxy,pending=True)))
		elif args.command=='ack':
			if args.markdown=='-':raise ValueError('Pass literal inline Markdown; acknowledgement stdin is not supported')
			text=args.markdown;ids=[store.resolve_id(token)for token in args.ids]if args.ids else[store.pending_ack()];task_options=args.task_id,args.task_title,args.task_type,args.task_scope,args.task_detail
			if any(value is not None for value in task_options):
				if len(args.ids)!=1 or not all(task_options):raise ValueError('A combined acknowledgement needs one explicit message ID and all five task options')
				details=task_steps(args.task_detail);check_task_steps(details)
				with store.transaction()as shared:store.write_task(args.task_id,args.task_title,details,task_type=args.task_type,scope=args.task_scope,shared=shared);patch_hints=store.acknowledge(ids,text,shared=shared);store.mark_task(ids[0],args.task_id,shared=shared)
			else:patch_hints=store.acknowledge(ids,text)
			short=[i[:6]for i in ids];print('ok '+' '.join(short))
			for hint in patch_hints:print(hint)
			untracked=[record_id for record_id in ids if not any(record.get('task_id')is not None for(_,record)in store.id_matches(record_id)if record['id']==record_id)]
			if untracked:print('link it with: '+'; '.join(f'arena-preview task set <id> "<title>" -m {i}'for i in untracked))
		elif args.command=='board-status':
			b_state=store.board_state();self_sid=b_state.get('branch')or'none';role=b_state.get('board_role')or'blocked';main_sid=b_state.get('main_session_id')or'none';print(f"board {self_sid} role={role} main={main_sid}")
			for row in b_state.get('sessions')or[]:
				if not isinstance(row,dict):continue
				label=row.get('board_id')or row.get('worker_id')or row.get('branch');r_role=row.get('role')or'blocked';r_status='polling'if row.get('polling')else row.get('status')or'active';print(f"  {label}\t{r_role}\t{r_status}\t{row.get('branch')or''}")
			pending_entries=[item for item in b_state.get('entries')or[]if isinstance(item,dict)and not item.get('acknowledged_at')]
			if pending_entries:
				print(f"unacked entries {len(pending_entries)}")
				for item in pending_entries:print(f"  {item.get('id')}\t{item.get('branch')} -> {item.get('to')or'*'}")
		elif args.command=='board-post':
			payload={'action':'post','text':args.text}
			if args.to is not None:payload['to']=args.to
			if args.file is not None:
				file_path=Path(args.file)
				if not file_path.is_file():raise ValueError(f"No such board attachment file: {file_path}")
				raw_bytes=file_path.read_bytes()
				try:content=raw_bytes.decode('utf-8');encoding='utf-8'
				except UnicodeDecodeError:content=base64.b64encode(raw_bytes).decode('ascii');encoding='base64'
				payload['file']={'name':file_path.name,'size':len(raw_bytes),'sha256':hashlib.sha256(raw_bytes).hexdigest(),'encoding':encoding,'content':content}
			prev_ids={str(e.get('id')or'')for e in store.board_state().get('entries')or[]if isinstance(e,dict)};updated_board=store.board_action(payload);new_entry=next((e for e in reversed(updated_board.get('entries')or[])if isinstance(e,dict)and str(e.get('id')or'')not in prev_ids),None);eid=(new_entry or{}).get('id')or'ok';dest=(new_entry or{}).get('to')or args.to;print(f"posted {eid} to {dest}")
		elif args.command=='board-wake':target=args.session or'all-polling';store.board_action({'action':'wake','target':target});print(f"woke {target}")
		elif args.command=='publish':
			metadata=None
			if args.submission_format is not None:
				with args.submission_format.open('rb')as stream:data=stream.read(MAX_REPORT+1)
				if len(data)>MAX_REPORT:raise ValueError('Submission format exceeds the report size limit')
				metadata=json.loads(data.decode('utf-8'))
			count=store.publish(args.id,args.title,args.source,submission_format=metadata,allow_unparsed_markers=args.allow_unparsed_markers);revision=store.report(args.id)['updated_at'];warnings=verify_publication(store,args.id,revision,count);plural=''if count==1 else's';print(f"Published {args.id}, {count} field{plural}. Verified.");src_path=Path(args.source);src_text=src_path.read_text('utf-8')
			if store.board_state().get('board_role')=='worker':raw_bytes=src_path.read_bytes();posted_board=store.board_action({'action':'post','to':'main','text':f"Worker report `{args.id}` ({args.title}, {count} field{plural}) staged for main triage.",'payload':{'report_id':args.id,'report_title':args.title,'field_count':count},'file':{'name':src_path.name,'size':len(raw_bytes),'sha256':hashlib.sha256(raw_bytes).hexdigest(),'encoding':'utf-8','content':src_text}});post_eid=(posted_board.get('entries')or[{}])[-1].get('id')or'ok';print(f"Staged {src_path.name} on board ({post_eid}) for main; wait via arena-preview poll.")
			parsed_questions=parse_fields(src_text)[1];recommendations=[f"{question['prompt']}: {', '.join(question['default'])}"for question in parsed_questions if question.get('default')]
			if recommendations:print('you recommended:\n'+'\n'.join(recommendations))
			missing_recommended=[question['id']for question in parsed_questions if question.get('type')in{'choice','checkbox'}and not question.get('default')]
			if missing_recommended:print('Warning: no recommended answer supplied for question ID(s): '+', '.join(missing_recommended)+'. Preselect a recommended option with (x) or [x].',file=sys.stderr)
			for warning in warnings or():print(f"{args.source}:{warning['line']}: Warning: possible unparsed {warning['marker']} marker. Keep spaces outside the markers. Literal marker text can be intentional.",file=sys.stderr)
			for warning in mermaid_report_warnings(str(args.source),src_text):print(warning,file=sys.stderr)
			if not count and'{#'in src_text:print('Warning: 0 fields parsed. A `{#id}` marker ends a prompt line, then the `- ( ) option` lines follow.',file=sys.stderr)
		elif args.command=='report-answer':answers=json.loads(args.answers_json);res=store.answer_worker_report(args.worker,args.report_id,answers);print(f"Answered {res['report_id']} for {res['worker']}.")
		elif args.command=='report-forward':res=store.forward_worker_report(args.worker,args.report_id,local_id=args.local_id,title_override=args.local_title);plural=''if res['fields']==1 else's';print(f"Forwarded {res['worker_report_id']} from {res['worker']} as {res['id']} ({res['fields']} field{plural}).")
		elif args.command=='report':
			held=False
			for record in store.stale_reports():
				report_id=record['id']
				try:removed=store.unpublish(report_id,stale_only=True)
				except ReportHeld as error:print(f"Skipped {report_id}: {error}",file=sys.stderr);held=True;continue
				if removed:print(f"Unpublished {report_id}. Answers, source kept.")
				else:print(f"Skipped {report_id}: no longer stale or published.")
			return 1 if held else 0
		elif args.command=='unpublish':report_id=store.resolve_id(args.report_id);store.unpublish(report_id);print(f"Unpublished {report_id}. Answers, source kept.")
		elif args.command=='stale-reports':
			reports=store.stale_reports();print(f"Stale reports {len(reports)}")
			for report in reports:print(f"{report['id']}\t{report['reason']}")
		elif args.command in{'report-pin','report-unpin'}:report_id=store.resolve_id(args.report_id);pinned=args.command=='report-pin';store.pin_report(report_id,pinned);print(f"{'Pinned'if pinned else'Unpinned'} {report_id}.")
		elif args.command=='report-state':
			rows=store.report_state(args.report_id)
			if args.report_id and not rows:print(f"No report {args.report_id}",file=sys.stderr);raise SystemExit(1)
			print(format_report_state(rows),end='')
		elif args.command=='task-show':print_task_row(store,args.task_id)
		elif args.command=='task':
			task_id=args.id_arg
			if not task_id:raise ValueError('A task needs an ID: arena-preview task set <id> "<title>" -m <msg-id>')
			if args.title_arg is None and not args.detail_arg and args.task_details is None and not args.append_details and args.status is None and args.order is None and args.blocked is None and args.report is None and args.msg_id is None and args.amend is None and args.task_type is None and args.task_scope is None and args.blocked_by is None:print_task_row(store,task_id);return 0
			details=args.task_details
			if details is None and args.detail_arg:details=args.detail_arg
			if details is not None:details=task_steps(details);check_task_steps(details);store.refuse_session_ids(details)
			check_task(task_id,args.title_arg,details)
			if args.report:
				try:store.report(args.report)
				except FileNotFoundError:raise ValueError(f"No report is stored under {args.report}")from None
				if args.blocked is None:args.blocked=True
			if args.amend:store.amend_task(store.resolve_id(args.amend),task_id)
			elif store.task_record(task_id)is None and args.task_scope is None and not(details and details[0].startswith(TASK_SCOPE_PREFIX)):raise ValueError('A new task needs an explicit scope (--scope)')
			if args.msg_id:
				with store.transaction()as shared:record=store.write_task(task_id,args.title_arg,details,args.status,args.order,args.blocked,args.report,task_type=args.task_type,blocked_by=args.blocked_by,shared=shared,append_details=args.append_details,scope=args.task_scope);record=store.mark_task(args.msg_id,task_id,shared=shared)or record
			else:record=store.write_task(task_id,args.title_arg,details,args.status,args.order,args.blocked,args.report,task_type=args.task_type,blocked_by=args.blocked_by,append_details=args.append_details,scope=args.task_scope)
			print(format_task(record))
		elif args.command=='task-unlink':record=store.unlink_submission(store.resolve_id(args.submission_id));print(f"Unlinked submission {record['id']} from tasks; history retained.")
		elif args.command=='task-remove':print(format_task(store.remove_task(store.resolve_id(args.task_id))))
		elif args.command=='task-list':print(format_task_list(store.list_tasks(),args.full))
		elif args.command=='import-state':
			if args.source:
				try:text=args.source.read_text(encoding='utf-8')
				except OSError as error:raise ValueError(f"No such state file: {error.filename or args.source}")from None
			else:text=sys.stdin.read()
			print(format_import_receipt(store.import_state(text,args.replace_tasks)))
		elif args.command=='compare-state':
			source_path=resolve_compare_state_source(state_dir,args.source)
			try:text=source_path.read_text(encoding='utf-8')
			except OSError as error:raise ValueError(f"No such state file: {error.filename or source_path}")from None
			print(format_state_comparison(store.compare_state(text),source=source_path))
		elif args.command=='save-state':saved=store.save_backup();print(f"Saved state to {saved['latest_path']} (notes {saved['notes']}, tasks {saved['tasks']}, reports {saved['reports']}, answers {saved['answers']}); queued local sync.")
		elif args.command=='restore-state':print(format_import_receipt(store.restore_state(previous=args.previous)))
		elif args.command=='clear-state':print(format_clear_state(store.clear_state()))
		elif args.command=='status-state':print(format_state_usage(store.state_usage()))
		elif args.command=='clean-state':print(format_clean_state(store.clean_state(dry_run=args.dry_run)))
		elif args.command=='init':
			if args.tz:
				if not zone_exists(args.tz):print(f"Unknown zone {args.tz}.",file=sys.stderr);return 1
				store.set_display_timezone(args.tz)
			print(f"state {store.path} {'created'if fresh_state else'present'}")
	except(OSError,ValueError,TypeError,KeyError,sqlite_api().Error,RuntimeError)as error:print(f"Preview error: {error}",file=sys.stderr);return 1
	return 0
if __name__=='__main__':ensure_installed();raise SystemExit(main())
