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
from datetime import datetime,timedelta,timezone
from fnmatch import fnmatchcase
from pathlib import Path
from urllib.parse import parse_qs,unquote,urlsplit,urlunsplit
from zoneinfo import ZoneInfo
HAS_RENDERER=None
sqlite3=None
def __getattr__(name):
	if name in('BaseHTTPRequestHandler','ThreadingHTTPServer'):from http import server;return getattr(server,name)
	raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
ASSETS=Path(__file__).resolve().parents[1]/'assets'
IDENTIFIER=re.compile('[a-zA-Z0-9_-]{1,80}\\Z')
VIOLATION_NOTES="type NOT IN ('', 'questionnaire')"
PENDING_SUBMISSIONS='acknowledged_at IS NULL AND seq IN (SELECT MAX(seq) FROM submissions GROUP BY report_id)'
REPLY_REFERENCE=re.compile('^RE:\\s*([a-zA-Z0-9_-]{1,80}(?:/[a-zA-Z0-9_-]{1,80})?)(?:\\s|$)',re.IGNORECASE)
MAX_REPORT=2000000
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
def read_set_files(root=None):
	base=Path(root or Path.cwd()).resolve();cached=READ_SET_CACHE.get(base)
	if cached is not None:return list(cached)
	files=[]
	if(base/'ARENA.md').is_file():files.append('ARENA.md')
	for(dirpath,dirnames,filenames)in os.walk(base,followlinks=False):
		dirnames[:]=sorted(name for name in dirnames if name not in WORKSPACE_SKIP)
		if'AGENTS.md'in filenames:files.append((Path(dirpath)/'AGENTS.md').relative_to(base).as_posix())
	for name in('.agents/skills/arena-skill/SKILL.md','skills/arena-skill/SKILL.md'):
		if(base/name).is_file():files.append(name);break
	READ_SET_CACHE[base]=tuple(files);return list(files)
def workspace_usage(root=None):
	root=Path(root or Path.cwd());total=0;count=0
	for(dirpath,dirnames,filenames)in os.walk(root,followlinks=False):
		dirnames[:]=[name for name in dirnames if name not in WORKSPACE_SKIP]
		for name in filenames:
			try:total+=(Path(dirpath)/name).stat().st_size
			except OSError:continue
			count+=1
	return{'bytes':total,'files':count,'documented_cap_bytes':SNAPSHOT_CAP_BYTES,'download_cap_bytes':None}
FENCE=re.compile('^ {0,3}(`{3,}|~{3,})')
CHOICE=re.compile('^\\s*[-*]\\s+\\(([ xX]?)\\)\\s+(\\S.*?)\\s*$')
CHECKBOX=re.compile('^\\s*[-*]\\s+\\[([ xX]?)\\]\\s+(\\S.*?)\\s*$')
BLANK=re.compile('^(?:(.*?)[\\s:])?_{3,}\\s*$')
ANCHOR=re.compile('\\s*\\{#([a-zA-Z0-9_-]{1,80})\\}\\s*$')
COMMENT_ANCHOR=re.compile('<!--\\s*(?:id\\s*[:=]\\s*|#)([a-zA-Z0-9_-]{1,80})\\s*-->',re.IGNORECASE)
MD_COMMENT_OR_CODE=re.compile('(`+)[\\s\\S]*?(?<!`)\\1(?!`)|<!--[\\s\\S]*?-->')
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
REMINDER_CHAR_CAP=88
REMINDERS='Bible absent? MUST read ARENA.md, all AGENTS.md, arena-skill SKILL.md fully first.','`task list` first; update as work changes.','Smallest open task next.','Always push.','`ask_user` on GH_TOKEN failure.','Docs: terse, clear.','Questions: ASAP, in reports.','Publish reports.','Never end turn with unblocked tasks.','Unpublish stale reports.','End turn with `poll`.','Grep-verify each edit.',TASK_REMINDER,'Before push: rebase `origin/main`; empty checks mean it moved.','Before pushed-turn end: check PR CI; read verdict on a later call, never loop.','Run `arena-preview`, not full path.','NEVER spam `run logs`.'
REMINDER_CURSOR='reminder_cursor'
POLLS_SINCE_MESSAGE='polls_since_message'
GATE_THRESHOLD=6
STALE_REPORT_THRESHOLD=10
DEFAULT_PORT=8000
AGENT_KEY_META='agent_key'
AGENT_KEY_EXPIRY_SECONDS=1200
AGENT_KEY_EXPIRY_META='agent_key_expired_at'
AGENT_SEEN_META='agent_seen_at'
AGENT_INTERRUPT_META='agent_interrupt'
AGENT_INTERRUPT_FLAG_META='agent_interrupt_flag'
VIOLATION_TYPES={'notes':('bash-violation','process-violation','missing-details','out-of-scope','task-not-created'),'downloads':('bash-violation','process-violation'),'reports':('format-violation','unnecessary-report'),'tasks':('current-task-violation','empty-task-list','missing-details','out-of-scope','task-not-created')}
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
def now():return datetime.now(timezone.utc).isoformat()
TIMEZONE_META='display_tz'
DISPLAY_ZONE=None
def zone_exists(name):
	try:ZoneInfo(name);return True
	except(KeyError,ValueError,OSError):return False
def resolve_display_zone(store):
	name=os.environ.get('TZ')or(store.meta_value(TIMEZONE_META)if store else None)
	if not name or not zone_exists(name):return None
	return ZoneInfo(name)
def local_stamp(value):
	if not value or DISPLAY_ZONE is None:return value
	try:parsed=datetime.fromisoformat(value)
	except ValueError:return value
	if parsed.tzinfo is None:parsed=parsed.replace(tzinfo=timezone.utc)
	return parsed.astimezone(DISPLAY_ZONE).isoformat()
def workspace_branch(root=None):
	try:head=(Path(root or Path.cwd())/'.git'/'HEAD').read_text(encoding='utf-8').strip()
	except OSError:return''
	prefix='ref: refs/heads/';return head[len(prefix):]if head.startswith(prefix)else''
def clear_skip_poll(db):db.execute('DELETE FROM meta WHERE key = ?',(SKIP_POLL_META,))
def reset_poll_count(db):
	if gate_pending_count(db)==1:db.execute("INSERT OR REPLACE INTO meta VALUES (?, '0')",(POLLS_SINCE_MESSAGE,))
def meta_number(db,key):row=db.execute('SELECT value FROM meta WHERE key = ?',(key,)).fetchone();value=str(row[0])if row else'';return int(value)if value.isdigit()else 0
def gate_pending_count(db):return db.execute(f"SELECT (SELECT count(*) FROM notes WHERE acknowledged_at IS NULL AND blocking = 1) + (SELECT count(*) FROM submissions WHERE {PENDING_SUBMISSIONS})").fetchone()[0]
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
			elif behind:return f"Remote branch {branch} is {behind} commit(s) behind origin/main. Check whether main absorbed its commits before rebasing."
		if main_identical(root):return''
		if git('rev-parse','--is-shallow-repository').stdout.strip()=='true':
			visible=git('rev-list','--count','origin/main').stdout.strip()
			if visible in{'','0','1'}:return''
		count=git('rev-list','--count','HEAD..origin/main')
		if count.returncode==0 and int(count.stdout.strip())>0:return f"Local check: origin/main carries {count.stdout.strip()} commit(s) this branch lacks. Replay your commits over main: run git fetch origin and git rebase origin/main, then push."
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
			name,_,rest=row.partition('\t');stamp,_,conclusion=rest.partition('\t')
			if name and(name not in newest or stamp.rstrip('Z')>newest[name][0]):newest[name]=stamp.rstrip('Z'),conclusion
		failed=[name for(name,(_,conclusion))in newest.items()if conclusion in FAILED_CONCLUSIONS];return', '.join(failed)
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
		try:stamp=datetime.fromisoformat(node['at'].replace('Z','+00:00'))
		except ValueError:return None
		if stamp.tzinfo is None:return None
		key=node['type'],node['name'],node['workflow']
		if key not in latest or stamp>latest[key][0]:latest[key]=stamp,node
	return[node for(_stamp,node)in sorted(latest.values(),key=lambda entry:(entry[1]['workflow'],entry[1]['name'],entry[1]['type']))]
def pr_check_rollup(root,slug,sha):
	latest=pr_check_latest(root,slug,sha)
	if latest is None:return None
	states={node['state']for node in latest}
	if states&set(PR_CHECK_FAILURE_STATES):return'FAILURE',len(latest)
	if not states or states-set(PR_CHECK_SUCCESS_STATES):return'PENDING',len(latest)
	return'SUCCESS',len(latest)
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
def pr_checks_watch(root='.',seconds=PR_CHECK_DISCOVERY_SECONDS,step=PR_CHECK_DISCOVERY_STEP,sleep=None,progress=None):
	pause=sleep or time.sleep
	def unavailable(reason):return'unavailable',[f"PR CHECKS UNAVAILABLE: {reason}."]
	branch=workspace_branch(root);slug=repo_slug(root)
	if not branch or not slug:return unavailable('no branch pull request to watch')
	pinned='';started=.0;seen_any=False;reported=set();lines=[]
	while True:
		head=pr_head_sha(root,branch,slug)
		if head is None:return unavailable('gh did not read the pull request head')
		if not head:return unavailable('no branch pull request to watch')
		if head!=pinned:pinned=head;started=time.monotonic();seen_any=False;reported=set();lines=[]
		latest=pr_check_latest(root,slug,pinned)
		if latest is None:return unavailable('gh did not read the check rollup')
		elapsed=time.monotonic()-started
		if latest:seen_any=True
		failed=[node for node in latest if node['state']in PR_CHECK_FAILURE_STATES]
		for node in failed:
			identity=node['type'],node['workflow'],node['name'],node['at']
			if identity in reported:continue
			reported.add(identity);line=f"Failed so far: {pr_check_label(node)}";lines.append(line)
			if progress is not None:progress(line)
		if not seen_any:
			if elapsed>=seconds:return'not_started',[f"PR CHECKS NOT STARTED: no visible checks appeared on {pinned[:7]} within {seconds} seconds."]
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
			if pattern.search(line):type,ident=tokens[token];hits.append((path,number,token,type,ident))
	return hits
def commit_id_report(hits):
	if not hits:return None
	lines=['COMMIT BLOCKED: the staged change cites a session-local ID. That ID exists only in this session, so a reader of the repository cannot resolve it.']
	for(path,number,token,type,ident)in hits:named=ident if token==ident else f"{token} of {ident}";lines.append(f"  {path}:{number}: {type} {named}")
	lines.append('Strip the citation, then commit again.');renames={ident for(_,_,_,type,ident)in hits if type in{'task','report'}}
	if renames:lines.append('If this is a name collision, rename the preview record, not repository content:');lines.extend(f"  arena-preview rename {shlex.quote(ident)} NEW-ID"for ident in sorted(renames));lines.append('Replace NEW-ID with an unused preview ID.')
	return'\n'.join(lines)
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
CLAUSE_ACK=re.compile('([\\w./-]+\\.md):([0-9]+):\\s*\\S')
def clause_ack_error(text,root=None):
	base=Path(root or Path.cwd());files=read_set_files(base)
	if not files:base=Path.cwd();files=read_set_files(base)
	paths={Path(path).name:base/path for path in files}
	for match in CLAUSE_ACK.finditer(text or''):
		path=paths.get(Path(match.group(1)).name)
		if path is None or not path.is_file():continue
		if int(match.group(2))<=len(path.read_text(encoding='utf-8').splitlines()):return''
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
def strip_md_comments(text):return MD_COMMENT_OR_CODE.sub(lambda match:match.group(0)if match.group(1)else'',text)
def find_comment_anchor(text):
	found=None
	for match in MD_COMMENT_OR_CODE.finditer(text):
		if match.group(1):continue
		anchor_match=COMMENT_ANCHOR.fullmatch(match.group(0).strip())
		if anchor_match:found=anchor_match.group(1)
	return found
def strip_fenced_md_comments(markdown,keep_anchors=False):
	lines=markdown.splitlines(keepends=True);out,chunk,fence=[],[],None
	def flush():
		if not chunk:return
		segment=''.join(chunk);out.append(MD_COMMENT_OR_CODE.sub(lambda match:match.group(0)if match.group(1)or keep_anchors and COMMENT_ANCHOR.fullmatch(match.group(0).strip())else'',segment));chunk.clear()
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
	markdown=strip_md_comments(markdown)
	try:from markdown_it import MarkdownIt
	except ImportError:return html.escape(markdown)
	parser=MarkdownIt('commonmark',{'html':False}).enable(['strikethrough']);highlight_markdown(parser);parser.add_render_rule('link_open',open_link);return parser.renderInline(markdown)
def field_html(question):
	prompt=html.escape(question['prompt'],quote=True);name=html.escape(question['id'],quote=True);body=f'<div class="question" data-field="{name}"';body+=f' data-type="{question["type"]}">';body+=f'<small class="question-id">Question ID: <code>{name}</code></small>'
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
TASK_ORDER_OWNED_META='task_order_owned'
TASK_ORDER_PENDING_META='task_order_notice_pending'
TASK_ORDER_NOTICE='Task order changed in the preview; preserve its upcoming task order.'
ECHO_DETAIL=200
TASK_COLUMNS='id, title, details, status, position, updated_at, completed_at, blocked, report_id, type'
TASK_ORDER="status DESC, CASE WHEN status = 'finished' THEN completed_at END DESC, CASE WHEN status = 'finished' THEN id END DESC, (blocked = 0 OR report_id IS NULL), position, id"
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
		ready=[item for item in pending if all(b in done for b in waiting[item['id']])]
		if not ready:placed.extend(pending);break
		for item in ready:placed.append(item);done.add(item['id']);pending.remove(item)
	return placed
UNLINKED_TASK_ID=''
def link_task_answers(db,records):
	links=db.execute('SELECT id, report_id, task_id, text, at FROM submissions ORDER BY at, seq').fetchall();answers=[{'id':link['id'],'at':clip_stamp(link['at']),'text':link['text'],'report_id':link['report_id'],'task_id':link['task_id']}for link in links]
	for item in records:
		linked=[{'id':answer['id'],'at':answer['at'],'text':answer['text'],'report_id':answer['report_id']}for answer in answers if answer['task_id']!=UNLINKED_TASK_ID and(answer['task_id']==item['id']or answer['report_id']and answer['report_id']==item.get('report_id'))]
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
	type=item.get('type')or'note';display_type='answer'if type=='report'else type;header='`'+str(item.get('id',''))[:6]+'`'
	if display_type!='note':header+=f" {display_type}"
	if item.get('quiet')and not item.get('blocking'):header+=' quiet (no ack required)'
	if item.get('report_id'):header+=f" report {item['report_id']}"
	lines=[header];text=item.get('text')or''
	if text:lines.append(text)
	attachments=item.get('attachments')or[]
	if attachments:names=', '.join(attachment_line(record)for record in attachments);lines.append(f"attachments: {names}")
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
def format_task_list(tasks,full=False):
	shown=tasks if full else[t for t in tasks if t.get('status')=='upcoming'];lines=[f"Tasks {len(shown)}"if full else f"Tasks {len(shown)} upcoming"]
	if any(t.get('blocked')for t in shown):lines.append('* = blocked on a report answer or linked run')
	head=None if full else current_task(shown)
	for task in shown:
		order=task.get('order');status=task.get('status');is_current=head is not None and task.get('id')==head.get('id')
		if is_current:status='current'
		id_=task.get('id');blocked=task.get('blocked');report_id=task.get('report_id');blocked_mark='*'if blocked else'';line=f"{order}{blocked_mark}  {status} {id_}"
		if report_id:line+=f" | waits on {report_id}"
		if task.get('runs'):line+=f" | runs {', '.join(task['runs'])}"
		if task.get('type')and task.get('type')!=TASK_UNSPECIFIED:line+=f" | type {task.get('type')}"
		lines.append(line)
		for detail in task.get('details',[]):lines.append(f"    {detail}")
		for answer in task.get('answers',[]):lines.append(f"    answer {answer['id']}, at {answer['at']}");lines.append(f"    {answer['text']}")
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
		for answer in record.get('answers',[]):lines.extend([f"answer {answer['id']}, at {answer['at']}",answer['text']])
	return'\n'.join(lines)
def print_task_row(store,task_id):
	if not store.id_matches(task_id):raise ValueError(f"No task is stored under {task_id}.")
	resolved=store.resolve_id(task_id);tasks=store.tasks()or{};record=next((item for status in TASK_STATUSES for item in tasks.get(status,[])if item['id']==resolved),None)
	if record is None:raise ValueError(f"No task is stored under {resolved}.")
	print(format_task(record,full=True))
def format_key(record):
	lines=[]
	if record.get('key'):lines.append(f"key {record['key']}")
	if record.get('host'):lines.append(f"host {record['host']}");lines.append(f"use fetch_page on {record['host']}/v1/<route>?key=<key>, never bash")
	if record.get('at'):lines.append(f"at {local_stamp(record['at'])}")
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
def preview_server_available(store):
	port=store.meta_value('port')
	if not port:return False
	with closing(socket.socket())as probe:probe.settimeout(1);return probe.connect_ex(('127.0.0.1',int(port)))==0
def require_server(store):
	port=store.meta_value('port')
	if not port:raise ValueError('Preview down. Run arena-preview serve.')
	if preview_server_available(store):return
	raise ValueError(f"Preview down. Before polling: arena-preview serve --port {port}")
def print_read(store):
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
	moment=moment or datetime.now(DISPLAY_ZONE or timezone.utc)
	if moment.tzinfo is None:moment=moment.replace(tzinfo=timezone.utc)
	minute=moment.minute//READ_SET_INTERVAL_MINUTES*READ_SET_INTERVAL_MINUTES;return moment.replace(minute=minute,second=0,microsecond=0).isoformat()
def read_set_paths(root=None):
	root=Path(root or resolve_repo_root()).resolve();agents=[]
	for(dirpath,dirnames,filenames)in os.walk(root,followlinks=False):
		dirnames[:]=sorted(name for name in dirnames if name not in WORKSPACE_SKIP)
		if'AGENTS.md'in filenames:agents.append(Path(dirpath)/'AGENTS.md')
	skill=root/'.agents'/'skills'/'arena-skill'/'SKILL.md';return[path for path in[root/'ARENA.md',*sorted(agents),skill]if path.is_file()]
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
def idle_wait(line):
	heads=[head for head in map(piece_head,command_pieces(agent_line(line)))if head]
	if'sleep'not in heads:return None
	if any(head in('arena-preview','preview.py')for head in heads):return'WAIT BLOCKED: a sleep beside a preview call holds the turn. Take the next queued task, or read the run on a later call. A routed run keeps its verdict, and `arena-preview poll` waits when the queue is empty.'
	if any(token.strip('"\'').endswith('.log')for piece in command_pieces(agent_line(line))for token in piece.split()):return'SLEEP BLOCKED: a sleep beside a .log read holds the turn. Use `arena-preview poll` to wait, and `arena-preview logs <name>` to read a run.'
	return None
def quiet_inbox_line(line):return first_blocking_piece(line)is None
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
	lexer=shlex.shlex(agent_line(line),posix=False,punctuation_chars=';&|()\n');lexer.whitespace=' \t\r';lexer.whitespace_split=True
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
	def paths(words,recursive=False):
		nonlocal protected
		if protected is None:protected={path.resolve():path.relative_to(base).as_posix()for path in read_set_paths(base)}
		found=set()
		for word in words:candidate=(here/Path(word).expanduser()).resolve();found.update(path for path in protected if path==candidate or recursive and candidate in path.parents)
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
			files=[];pattern=head!='grep';recursive=False;flags=True;index=0
			while index<len(words):
				word=words[index];index+=1
				if word in('<','>','>>','<<','<<-','<<<','<&','>&'):
					if word=='<':files.extend(words[index:index+1])
					index+=1
				elif flags and word=='--':flags=False
				elif flags and word.startswith('-'):
					if head=='grep':
						recursive|=word in('--recursive','--dereference-recursive')or not word.startswith('--')and any(flag in word[1:]for flag in'rR')
						if word in('-e','--regexp','-f','--file'):
							if word in('-f','--file'):files.extend(words[index:index+1])
							pattern=True;index+=1
						elif word.startswith(('-e','--regexp=','-f','--file=')):
							if word.startswith('-f'):files.append(word[2:])
							elif word.startswith('--file='):files.append(word.split('=',1)[1])
							pattern=True
					elif word in('-n','-c','--lines','--bytes'):index+=1
				elif not pattern:pattern=True
				else:files.append(word)
			sources=paths(files,recursive)|incoming
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
	seconds=round(time.monotonic()-started,1);output=(completed.stdout+completed.stderr).splitlines();tail=clean_run_tail(output[-RUN_TAIL_LINES:]);verdict=tail[-1]if tail else'(no output)';path=run_record_path(state_dir,name);existing=json_record(path)or{};record={**existing,'run_id':existing.get('run_id')or uuid.uuid4().hex,'name':name,'argv':argv,'exit':completed.returncode,'seconds':seconds,'finished_at':now(),'verdict':verdict,'tail':tail,'announced':False};write_run_record(path,record)
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
def link_task_runs(records,state_dir):
	linked=live_task_runs(state_dir)
	for record in records:
		names=linked.get(record.get('id'))
		if names:record['runs']=names;record['blocked']=True
		else:record.pop('runs',None)
	return records
def guard_task_runs(state_dir,task_id=None,action='change'):
	linked=live_task_runs(state_dir);selected=linked if task_id is None else{task_id:linked.get(task_id,[])};held=[(identifier,names)for(identifier,names)in selected.items()if names]
	if not held:return
	labels='; '.join(f"{identifier}: {', '.join(names)}"for(identifier,names)in sorted(held));raise ValueError(f"Cannot {action}; live task run(s): {labels}")
def skip_poll_ready(store):return store.skip_poll_requested()and not live_runs(store.path.parent)
def run_notices(state_dir,show_running=False,out=None):
	out=sys.stdout if out is None else out;runs=Path(state_dir)/'runs'
	if not runs.is_dir():return 0
	records=[]
	for path in sorted(runs.glob('*.json')):
		record=json_record(path)
		if record is not None:records.append((path,reconcile_routed_record(path,record)))
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
WORKTREE_STEP='NEXT: work queued file task in worktree while this runs: `arena-preview worktree start [--gate <gate-name>] [--allow <glob>] <name> <destination>`.'
DETACHED_NOTICE='RUN DETACHED: `{name}` runs in background, call ends clean. Verdict on later call. '+WORKTREE_STEP
DETACHED_BLOCK='RUN BLOCKED: background start failed, wait not started. Use `arena-preview run start {name} -- <the same line>` instead.'
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
def detached_seconds(started_at):
	try:
		started=datetime.fromisoformat(str(started_at))
		if started.tzinfo is None:started=started.replace(tzinfo=timezone.utc)
		return max(0,int((datetime.now(timezone.utc)-started).total_seconds()))
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
	if command is not None and line.strip()!=command.strip():print(f"RUN BLOCKED: automatic routing would replay a mixed shell line. Use `arena-preview run start {name} -- <remaining command>`; do not repeat completed commands.",file=sys.stderr,flush=True);return PR_CHECK_REFUSED
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
def detach_run(state_dir,name,argv,task_id=None,after=None):
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
	task_id=run_task_id(state_dir,task_id)
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
def route_command(state_dir,name,argv,task_id=None,after=None):
	if not RUN_NAME.fullmatch(name or''):print('arena-preview run start: the name takes 1 to 64 letters, digits, dot, dash or underscore.',flush=True);return 2
	if argv[:1]==['--']:argv=argv[1:]
	if not argv:print('arena-preview run start: name a command after --, for example: run start gates check',flush=True);return 2
	try:clear_poll_repeat(Store(state_dir,create=False))
	except FileNotFoundError:pass
	if not detach_run(state_dir,name,argv,task_id=task_id,after=after):print(f"arena-preview run start: the start of `{name}` failed.",flush=True);return 1
	print(f"routed {name}: background run. Verdict on later call. {WORKTREE_STEP}",file=sys.stderr,flush=True);return 0
def next_open_task(state_dir,gate_name):
	try:store=Store(state_dir)
	except FileNotFoundError:return None
	held=str(routed_record(state_dir,gate_name).get('task')or'')
	for task in store.list_tasks():
		if task.get('status')!='upcoming'or task.get('id')==held:continue
		if gate_name in(task.get('runs')or[]):continue
		return task
	return None
def next_task_hint(state_dir,gate_name):
	task=next_open_task(state_dir,gate_name)
	if task is None:return''
	return f" Next open task: {task['id']} \"{task.get('title')or''}\". Start it in a worktree: `arena-preview worktree start --gate {gate_name} --allow '<task path glob>' {task['id']} <destination outside the repo root>`."
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
def poll_inbox(store,sleeper=None):
	if sleeper is None:sleeper=time.sleep
	deadline=time.monotonic()+POLL_WAIT_SECONDS;listing={'checked_at':None,'pending':[]};print(f"poll max {POLL_WAIT_SECONDS} s; bash timeout 1800 s.",file=sys.stderr,flush=True);store.start_poll();failures=[];stop_checks=threading.Event();threading.Thread(target=pr_check_watch,args=(failures,resolve_repo_root(),stop_checks),daemon=True).start()
	try:
		for index in range(POLL_MAX_LOOPS):
			if time.monotonic()>=deadline:break
			violation=store.pending_violation_note();listing=store.read(include_quiet=False)
			if violation or listing['pending']:print_read(store);return 0
			if run_notices(store.path.parent):clear_poll_repeat(store);print('DO NOT END TURN. POLL AGAIN.',flush=True);return 0
			if failures:print(f"PR CHECKS FAILED: {failures[0]}. Fix the failed check before the next push.",flush=True);return 0
			open_tasks=[item for item in store.list_tasks()if item['status']=='upcoming'and not item['blocked']]
			if open_tasks:listing['tasks']=open_tasks;names=', '.join(item['id']for item in open_tasks);continue_line=f"CONTINUE: UNBLOCKED TASK {names}. Start a worktree with: arena-preview worktree start --gate <gate> <name> <destination>.";print(continue_line,flush=True);print(format_task_list(listing.get('tasks',[])),flush=True);return 0
			if skip_poll_ready(store):store.take_skip_poll();store.mark_turn_ended();print('SKIP: owner pressed Skip poll. End the turn, no second poll.',flush=True);print(format_read(listing),flush=True);return 0
			if index+1<POLL_MAX_LOOPS:
				remaining=deadline-time.monotonic()
				if remaining<=0:break
				sleeper(min(POLL_INTERVAL,remaining));store.stamp_polling()
	finally:stop_checks.set();store.clear_polling()
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
def questionnaire_command(state_dir,answers):
	import questionnaire
	if answers is None:print(questionnaire.INSTRUCTIONS);print(json.dumps(questionnaire.SPEC,indent=2,ensure_ascii=False));print(json.dumps(questionnaire.template(),indent=2));return 0
	from http.client import HTTPConnection,HTTPException;store=Store(state_dir);require_server(store)
	try:
		with closing(HTTPConnection('127.0.0.1',int(store.meta_value('port')),timeout=15))as client:
			client.request('POST','/api/questionnaire',answers.encode('utf-8'),{'Content-Type':'application/json'});response=client.getresponse();body=response.read()
			if response.status!=200:error=json.loads(body).get('error','Request rejected');raise ValueError(f"Questionnaire submission: HTTP {response.status}: {error}")
	except HTTPException as error:raise ValueError(f"Questionnaire submission failed: {error}")from None
	print('Questionnaire received.');return 0
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
def render_report_block(markdown):markdown=strip_fenced_md_comments(markdown);tagged,held=hold_out(markdown,'(?:\\s*(?:</?(?:ul|ol|li)>)\\s*){2,}',lambda match:match.group(0).strip().lower(),'listtag');out=put_back(render(tagged),held,'listtag');out=re.sub('<p>(<(?:ul|ol)>)','\\1',out);out=re.sub('(</(?:ul|ol)>)</p>','\\1',out);return out
def render_report(markdown):
	blocks,questions=parse_fields(markdown);parts=[]
	for(type,item)in blocks:
		if type=='markdown':
			if item.strip():parts.append(render_report_block(item))
		else:parts.append(field_html(item))
	return''.join(parts),questions
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
		directory=Path(directory).resolve();self.path=directory/'state.sqlite3';self.save_path=Path(save_path)if save_path else self.path.parent/SAVED_STATE;existed=self.path.is_file()
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
		listed='; '.join(f"{type} {record['id']} {(record.get('title')or record.get('text')or'')[:40]!r}"for(type,record)in matches);raise ValueError(f"{token} matches {len(matches)} records: {listed}. Give the full ID of one")
	def list_note_ids(self,page,page_size):
		if page<1 or not 1<=page_size<=100:raise ValueError('Page must be at least 1; page size must be 1 to 100')
		with closing(self.connect())as db:
			db.execute('BEGIN');total=db.execute('SELECT count(*) FROM notes').fetchone()[0];offset=(page-1)*page_size;ids=[]
			if offset<total:ids=[row[0]for row in db.execute('SELECT id FROM notes ORDER BY at DESC, seq DESC LIMIT ? OFFSET ?',(page_size,offset))]
		return ids,total
	def notes_matching(self,token):
		with closing(self.connect())as db:return[message_row(row)for row in db.execute('SELECT * FROM notes WHERE id = ? OR id LIKE ? ORDER BY at DESC',(token,f"{token}%")).fetchall()]
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
	def schedule_read_set_note(self,moment=None,root=None):
		moment=moment or datetime.now(DISPLAY_ZONE or timezone.utc)
		if moment.tzinfo is None:moment=moment.replace(tzinfo=timezone.utc)
		slot=read_set_slot(moment);paths=read_set_paths(root)
		with closing(self.connect())as db,db:
			db.execute('BEGIN IMMEDIATE');heartbeat=db.execute('SELECT value FROM meta WHERE key = ?',(POLLING_META,)).fetchone();age=seconds_since(heartbeat['value']if heartbeat else None)
			if age is not None and 0<=age<POLLING_FRESH_SECONDS:return None
			row=db.execute('SELECT * FROM notes WHERE quiet = 1 AND blocking = 1 AND acknowledged_at IS NULL ORDER BY at, seq LIMIT 1').fetchone()
			if row:return message_row(row)
			recorded=db.execute('SELECT value FROM meta WHERE key = ?',(READ_SET_LAST_META,)).fetchone()
			if recorded is None:recorded=db.execute('SELECT value FROM meta WHERE key = ?',(READ_SET_SLOT_META,)).fetchone()
			try:previous=datetime.fromisoformat(recorded['value'])if recorded else None
			except ValueError:previous=None
			if previous is None:db.execute('INSERT OR REPLACE INTO meta VALUES (?, ?)',(READ_SET_LAST_META,moment.isoformat()));return None
			if previous.tzinfo is None:previous=previous.replace(tzinfo=timezone.utc)
			if(moment-previous).total_seconds()<READ_SET_INTERVAL_MINUTES*60:return None
			for _ in range(MINT_ATTEMPTS):
				note_id=new_id()
				if not any(db.execute(f"SELECT 1 FROM {table} WHERE id = ?",(note_id,)).fetchone()for table in('notes','submissions','uploads','reports','tasks')):break
			else:raise RuntimeError('The state holds no free six-character ID for a bible note')
			listed='\n'.join(f"- `{path}`"for path in paths);text=f"Bible checkpoint after a {READ_SET_INTERVAL_MINUTES}-minute interval. Read each listed path in full with the read tool only if its current full text is absent from context, then acknowledge this note.\nAnswer the questionnaire only if any required bible text was absent from context before this check.\n{listed}";record=self.note(note_id,text,shared=db,autosave=False,quiet=True,blocking=True);db.execute('INSERT INTO meta(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value',(READ_SET_SLOT_META,slot));db.execute('INSERT INTO meta(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value',(READ_SET_LAST_META,moment.isoformat()))
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
			polling=self.polling();runs=live_runs(self.path.parent)if polling else[];return{'notes':notes,'reports':reports,'tasks':tasks,'uploads':uploads,'fetch_jobs':fetch_jobs,'workspace':workspace_usage(),'last_check':clip_stamp(meta.get('last_check')),'polling':polling,'waiting_for_gates':bool(runs),'checking_pr':any(pr_checks_command(routed_record(self.path.parent,name).get('argv',[]))for name in runs),'polling_since':clip_stamp(meta.get(POLL_SINCE_META))if polling else None,'skip_poll':clip_stamp(meta.get(SKIP_POLL_META)),'agent_interrupt_pending':bool(meta.get(AGENT_INTERRUPT_FLAG_META)),'read_set':read_set_files(self.repository_root()),'calls_since_message':meta_number(db,POLLS_SINCE_MESSAGE),'agent_key':self.agent_key(),'agent_seen_at':clip_stamp(meta.get(AGENT_SEEN_META)),'turn_ended_at':clip_stamp(meta.get(TURN_ENDED_META)),'agent_call_ended_at':clip_stamp(meta.get(AGENT_CALL_ENDED_META)),'gate_threshold':GATE_THRESHOLD}
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
	def task_blockers(self,task_id):
		with closing(self.connect())as db:rows=db.execute('SELECT blocked_by FROM task_dependencies WHERE task_id = ? ORDER BY blocked_by',(task_id,)).fetchall()
		return[row[0]for row in rows]
	def all_task_blockers(self):
		edges={}
		with closing(self.connect())as db:rows=db.execute('SELECT task_id, blocked_by FROM task_dependencies ORDER BY blocked_by').fetchall()
		for row in rows:edges.setdefault(row[0],[]).append(row[1])
		return edges
	def check_dependency(self,task_id,blocker,edges):
		if blocker==task_id:raise ValueError(f"{task_id} cannot wait on itself")
		if not self.task_record(blocker):raise ValueError(f"No task holds {blocker}; add it before another waits on it")
		pending=[blocker];seen=set()
		while pending:
			node=pending.pop()
			if node==task_id:raise ValueError(f"{blocker} already waits on {task_id}; that edge would close a cycle")
			if node in seen:continue
			seen.add(node);pending.extend(edges.get(node,[]))
	def write_dependencies(self,db,task_id,blocked_by):
		db.execute('DELETE FROM task_dependencies WHERE task_id = ?',(task_id,))
		for blocker in blocked_by:db.execute('INSERT OR IGNORE INTO task_dependencies (task_id, blocked_by) VALUES (?, ?)',(task_id,blocker))
	def refuse_session_ids(self,details):
		tokens={token:found for(token,found)in session_id_tokens(self).items()if NOTE_ID.fullmatch(token)or HEX7_RE.fullmatch(token)}
		for detail in details:
			for(token,(type,ident))in tokens.items():
				if re.search(f"(?<![0-9A-Za-z]){re.escape(token)}(?![0-9A-Za-z])",detail):named=ident if token==ident else f"{token} of {ident}";raise ValueError(f"That detail cites the session-local {type} {named}. A repository reader cannot resolve that ID. Describe the work instead")
	def write_task(self,task_id,title=None,details=None,status=None,order=None,blocked=None,report_id=None,completed_at=None,task_type=None,blocked_by=None,shared=None,append_details=False,restoring=False):
		details=[str(item)for item in details if str(item).strip()]if details else None
		if details and not restoring:self.refuse_session_ids(details)
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
			title=title if title is not None else stored['title']
			if append_details:details=(stored['details']if stored else[])+details;check_task(task_id,title,details)
			elif details is None:details=stored['details']if stored else[]
			if not details:raise ValueError('A task needs at least one detail')
			status=status or(stored['status']if stored else'upcoming')
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
				blocked_by=[str(item)for item in blocked_by if str(item).strip()];edges=self.all_task_blockers()
				for blocker in blocked_by:self.check_dependency(task_id,blocker,edges)
				self.write_dependencies(db,task_id,blocked_by)
			if report_wait and report_id and blocked and self.report_has_answers(db,report_id):blocked=False
			if report_id and blocked and(not stored or not stored['blocked']or stored.get('report_id')!=report_id):
				report=db.execute('SELECT markdown FROM reports WHERE id = ?',(report_id,)).fetchone()
				if report is not None and not parse_fields(report['markdown'])[1]:raise ValueError('A blocked task needs a report with answer fields')
			if status=='finished':guard_task_runs(self.path.parent,task_id,'finish task')
			if status=='finished'and blocked:raise ValueError(f"Task {task_id} is blocked; clear the report wait with --unblocked first")
			siblings=[task_row(item)for item in db.execute(f"SELECT {TASK_COLUMNS} FROM tasks WHERE status = ? AND id != ? ORDER BY position, id",(status,task_id)).fetchall()];record={'id':task_id,'title':title,'details':details,'status':status,'order':len(siblings)+1,'updated_at':stamp,'completed_at':completed_at,'blocked':bool(blocked),'report_id':report_id,'type':task_type};blockers=self.task_blockers(task_id)
			if blockers:record['blocked_by']=blockers
			if order is not None:index=max(0,min(order-1,len(siblings)))
			elif stored and stored['status']==status:index=max(0,min(stored['order']-1,len(siblings)))
			else:index=len(siblings)
			if not restoring and stored and stored['status']==status=='upcoming'and order is not None and index+1!=stored['order']and meta_number(db,TASK_ORDER_OWNED_META):raise ValueError('Task order belongs to the preview; omit --order for this update')
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
			row=db.execute('SELECT id, title, details, status, position, created_at, completed_at, blocked, report_id FROM tasks WHERE id = ?',(prev_id,)).fetchone()
			if row is None:raise ValueError(f"No task is stored under {prev_id}")
			guard_task_runs(self.path.parent,prev_id,'rename task')
			if db.execute('SELECT 1 FROM tasks WHERE id = ?',(task_id,)).fetchone():raise ValueError(f"A task is already stored under {task_id}")
			self.refuse_shared_id(db,'tasks','reports',task_id);db.execute('DELETE FROM tasks WHERE id = ?',(prev_id,));db.execute('INSERT INTO tasks (id, title, details, status, position, created_at, updated_at, completed_at, blocked, report_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',(task_id,row[1],row[2],row[3],row[4],row[5],stamp,row[6],row[7],row[8]));self.renumber(db)
	def userscript_history(self,shared=None):
		with self.transaction(shared,autosave=False)as db:row=db.execute('SELECT value FROM meta WHERE key = ?',(AUTOSAVE_HISTORY,)).fetchone();return json.loads(row[0])if row else[]
	def userscript_scope(self):root=self.path.parent.parent;return{'repo':root.name,'branch':workspace_branch(root)or''}
	def userscript_snapshot(self):core,counts=self.state_lines(self.state()['notes'],self.tasks()or{});core,stamp=autosave_core_and_stamp(core);scope=self.userscript_scope();history=self.userscript_history();stamps=self.newest_stamps();rows=[row for row in history if row['scope']==scope and row.get('stamp')];return{'save_id':str(uuid.uuid4()),'scope':scope,'core':core,'digest':autosave_digest(core),'history':rows,'lastsave':rows[-1]['stamp']if rows else None,'beforelastsave':rows[-2]['stamp']if len(rows)>1 else None,'text':state_ndjson(core+rows),'counts':counts,'stamp':stamp,'stamps':stamps}
	def confirm_userscript_save(self,payload):
		save_id,scope=payload.get('save_id'),payload.get('scope')
		if not isinstance(save_id,str):raise TypeError('Autosave save ID must be a UUID string')
		uuid.UUID(save_id);key=autosave_scope_key(scope)
		if scope!=self.userscript_scope():raise ValueError('Autosave confirmation is outside this repository and branch')
		core=autosave_core(payload.get('core'));stamp=payload.get('stamp',payload.get('lastsave'))
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
				history=[item for item in history if item['scope']!=scope]+kept;db.execute('INSERT OR REPLACE INTO meta VALUES (?, ?)',(AUTOSAVE_HISTORY,json.dumps(history)));db.execute('INSERT OR REPLACE INTO meta VALUES (?, ?)',(AUTOSAVE_RECEIPTS,json.dumps(receipts)))
		return{'confirmed':True,'save_id':save_id,'digest':digest}
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
	def save_state(self,payload):
		if not isinstance(payload,dict):raise TypeError('Save a state object')
		notes=payload.get('notes');tasks=payload.get('tasks')
		if tasks is None:tasks={}
		lines,counts=self.state_lines(notes,tasks);path=self.save_path
		if path.parent!=Path('.'):path.parent.mkdir(parents=True,exist_ok=True)
		path.write_text(state_ndjson(lines+self.userscript_history()),encoding='utf-8');return{'path':str(path),**counts}
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
				if source!='direct'and not row['allow_proxy']:raise ValueError('Proxy fallback was not enabled for this URL')
				directory=self.path.parent/FETCH_DIR;directory.mkdir(parents=True,exist_ok=True,mode=448);target=directory/f"{job_id}{Path(cleaned).suffix[:16]}";save_body(target,data);target.chmod(384);stamp=now();db.execute("UPDATE fetch_jobs SET status = 'saved', source = ?, name = ?, type = ?, size = ?, sha256 = ?, file = ?, error = NULL, claim = NULL, lease_until = NULL, updated_at = ? WHERE id = ?",(source,cleaned,file_type,len(data),body_digest(data),target.name,stamp,job_id));message=f"Download: {cleaned} ({len(data)} B, {file_type}) from {urlsplit(row['url']).hostname} via {source} saved to {target}";db.execute('INSERT INTO notes (id, text, at) VALUES (?, ?, ?)',(job_id,message,stamp));result=db.execute('SELECT * FROM fetch_jobs WHERE id = ?',(job_id,)).fetchone()
		except Exception:
			if target is not None:target.unlink(missing_ok=True)
			raise
		self.autosave();return fetch_row(result,self.path.parent)
	def import_state(self,text,replace_tasks=False):
		records=parse_state_import(text);raw_history=[row for row in records if row.get('type')=='userscript-autosave'];history=[autosave_record(row)for row in raw_history];refreshed={raw_row['save_id']:(raw_row['digest'],row['digest'])for(raw_row,row)in zip(raw_history,history)if raw_row.get('digest')!=row['digest']}
		for row in history:
			if'receipts'in row:row['receipts']={save_id:refreshed[save_id][1]if save_id in refreshed and digest==refreshed[save_id][0]else digest for(save_id,digest)in row['receipts'].items()}
		records=[row for row in records if row.get('type')!='userscript-autosave'];groups={};imported_receipts={}
		for row in history:
			key=autosave_scope_key(row['scope']);groups.setdefault(key,[]).append(row);receipts=imported_receipts.setdefault(key,{})
			for(save_id,digest)in{**row.get('receipts',{}),row['save_id']:row['digest']}.items():
				if save_id in receipts and receipts[save_id]!=digest:raise ValueError('Conflicting autosave receipt digests')
				receipts[save_id]=digest
			if len(groups[key])>2 or len({item['save_id']for item in groups[key]})!=len(groups[key]):raise ValueError('Autosave history holds at most two distinct saves per scope')
		reports=[r for r in records if'markdown'in r and'text'not in r];tasks=[r for r in records if'markdown'not in r and'title'in r and'text'not in r];messages=[r for r in records if'markdown'not in r and('title'not in r or'text'in r)];guard_task_runs(self.path.parent,action='replace state')
		with self.transaction(autosave=False)as db:
			db.execute('BEGIN IMMEDIATE')
			if history:
				receipts={}
				for row in history:key=autosave_scope_key(row['scope']);receipts.setdefault(key,{}).update(row.get('receipts',{}));receipts[key][row['save_id']]=row['digest']
				db.execute('INSERT OR REPLACE INTO meta VALUES (?, ?)',(AUTOSAVE_HISTORY,json.dumps(history)));db.execute('INSERT OR REPLACE INTO meta VALUES (?, ?)',(AUTOSAVE_RECEIPTS,json.dumps(receipts)))
			for table in('notes','reports','submissions','tasks'):db.execute(f"DELETE FROM {table}")
			self.import_tasks(tasks,replace_tasks,autosave=False,shared=db)
			for record in messages:
				if'text'not in record or'title'in record:raise ValueError('Each record must be a note, task or report answer')
				options={key:record.get(key)for key in('acknowledged_at','ack_type','ack_text','ack_edited_at','seen_at','task_id','replies','ack_edited_seen_count')};args=[record['id']];writer=self.note
				if'report_id'in record:writer=self.submission;args.append(record['report_id']);options['answer_data']=record.get('answer_data')
				writer(*args,record['text'],record.get('at'),shared=db,autosave=False,**options)
			self.import_reports(reports,shared=db)
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
	def stamp_polling(self):
		with closing(self.connect())as db,db:db.execute('INSERT OR REPLACE INTO meta VALUES (?, ?)',(POLLING_META,now()))
	def start_poll(self):
		with closing(self.connect())as db,db:stamp=now();db.execute('INSERT OR REPLACE INTO meta VALUES (?, ?)',(POLLING_META,stamp));db.execute('INSERT OR REPLACE INTO meta VALUES (?, ?)',(POLL_SINCE_META,stamp))
	def clear_polling(self):
		with closing(self.connect())as db,db:db.execute('DELETE FROM meta WHERE key IN (?, ?)',(POLLING_META,POLL_SINCE_META))
	def polling(self):age=seconds_since(self.meta_value(POLLING_META));return age is not None and 0<=age<POLLING_FRESH_SECONDS
	def request_interrupt(self,tab,report_id=None,armed=True,user_flagged=True,reason=None,clause_file=None,note=None):
		if not armed:self.clear_meta(AGENT_INTERRUPT_FLAG_META);self.take_interrupt();return{'agent_interrupt_pending':False}
		if not isinstance(tab,str)or tab not in{'notes','reports','tasks','downloads'}:raise ValueError('tab must name a preview tab')
		if reason is not None and reason not in VIOLATION_TYPES[tab]:raise ValueError('reason must name a violation type for this tab')
		if clause_file not in(None,'')and clause_file not in read_set_files(self.repository_root()):raise ValueError('clause_file must name a path in the bible')
		if note is not None and not isinstance(note,str):raise ValueError('note must be text')
		cited=' '.join((note or'').split())
		if len(cited)>300:raise ValueError('note must contain at most 300 characters')
		evidence=[]
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
		notice='\n'.join([heading,*evidence,'The call-count gate blocks after 6 unacknowledged calls.','Think about which clause was violated.','Read the bible if absent from context.','Fix the violation, then ack with `file:ln: clause`.']);return self.flag_violation_note(notice,reason,clause_file,cited)
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
			uploads=db.execute('SELECT count(*) FROM notes JOIN uploads USING (id) WHERE acknowledged_at IS NULL AND blocking = 1').fetchone()[0];notes=db.execute('SELECT count(*) FROM notes WHERE acknowledged_at IS NULL AND blocking = 1').fetchone()[0]-uploads;reports=db.execute(f"SELECT count(*) FROM submissions WHERE {PENDING_SUBMISSIONS}").fetchone()[0];remaining=db.execute("SELECT count(*) FROM tasks WHERE status <> 'finished'").fetchone()[0];published=db.execute('SELECT count(*) FROM reports WHERE published_at IS NOT NULL').fetchone()[0];stale=self.stale_report_ids(db)if published>=STALE_REPORT_THRESHOLD else[];cursor=meta_number(db,REMINDER_CURSOR);polls=0 if advance and not notes+reports+uploads else meta_number(db,POLLS_SINCE_MESSAGE)+(1 if advance else 0);blocked=bool(gate_pending_count(db))and polls>=GATE_THRESHOLD
			if routine:db.execute('INSERT OR REPLACE INTO meta VALUES (?, ?)',(REMINDER_CURSOR,str(cursor+1)))
			if advance:db.execute('INSERT OR REPLACE INTO meta VALUES (?, ?)',(POLLS_SINCE_MESSAGE,str(polls)))
		counts=[f"{count} {type}{'s'if count!=1 else''}"for(count,type)in((notes,'note'),(reports,'answer'),(uploads,'upload'))if count];unacked=[f"unacked {', '.join(counts)}"]if counts else[]
		if blocked:head=['blocked']
		elif polls and counts:
			if polls>GATE_THRESHOLD//2:head=[f"bash blocked in {GATE_THRESHOLD-polls}"]
			else:head=[f"calls {polls}"]
		else:head=[]
		tail=reminder_tail(cursor,remaining,len(stale))if routine else'';parts=[' '.join([*head,*unacked]).strip()]
		if order_notice:parts.append(TASK_ORDER_NOTICE)
		parts.append(tail);return' | '.join(parts).strip(' |')
	def calls_since_message(self):
		with closing(self.connect())as db:return meta_number(db,POLLS_SINCE_MESSAGE)
	def gate(self,threshold=GATE_THRESHOLD,pending_only=False):
		with closing(self.connect())as db,db:pending=gate_pending_count(db);polls=meta_number(db,POLLS_SINCE_MESSAGE)
		if pending_only:return not pending
		return not(pending and polls>=threshold)
	def read(self,include_quiet=True):
		quiet_filter=' AND (quiet = 0 OR blocking = 1 OR seen_at IS NULL)'if include_quiet else' AND quiet = 0'
		with self.transaction(autosave=False)as db:
			pending=[message_row(row)|{'type':'note'}for row in db.execute(f"SELECT * FROM notes WHERE acknowledged_at IS NULL{quiet_filter} ORDER BY at, seq")];pending+=[message_row(row)|{'type':'report'}for row in db.execute(f"SELECT * FROM submissions WHERE {PENDING_SUBMISSIONS} ORDER BY at, seq")];notes={}
			for table in('notes','submissions'):
				for row in db.execute(f"SELECT * FROM {table} ORDER BY at, seq"):record=message_row(row);notes[record['id']]=record
			for row in db.execute('SELECT id, source FROM reports'):notes[row['id']]=dict(row)|{'type':'report','text':''}
			for row in db.execute(f"SELECT {TASK_COLUMNS} FROM tasks"):task=task_row(row);notes[task['id']]={'id':task['id'],'text':'\n'.join([task['title'],*task['details']])}
			attachments={}
			for row in db.execute('SELECT * FROM uploads ORDER BY seq'):record=upload_row(row,self.path.parent);attachments.setdefault(record['note_id'],[]).append(record)
			for record in notes.values():add_note_attachments(record,attachments.get(record['id'],[]))
			for item in pending:
				if item['type']=='note':
					add_note_attachments(item,attachments.get(item['id'],[]));thread=reply_thread(item,notes)
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
					if table=='notes':
						uploads=db.execute('SELECT * FROM uploads WHERE note_id = ? ORDER BY seq',(record_id,)).fetchall()
						if uploads:
							stored=db.execute('SELECT details FROM tasks WHERE id = ?',(task_id,)).fetchone();details=json.loads(stored['details'])if stored else[];paths=[f"Attachment: {upload_row(row,self.path.parent)['path']}"for row in uploads];added=[path for path in paths if path not in details]
							if added:return self.write_task(task_id,details=added,append_details=True,shared=db)
					return
		raise ValueError(f"Unknown note: {record_id}; no task marker written")
	def pending_ack(self):
		with self.connect()as db:rows=db.execute(f"SELECT id, at FROM notes WHERE acknowledged_at IS NULL AND blocking = 1 UNION ALL SELECT id, at FROM submissions WHERE {PENDING_SUBMISSIONS} ORDER BY at").fetchall()
		if not rows:raise ValueError('No pending note or answer to acknowledge')
		if len(rows)>1:listed=', '.join(row['id']for row in rows);raise ValueError(f"{len(rows)} items are pending: {listed}. Name the ID to answer one")
		return rows[0]['id']
	def acknowledge(self,ids,text,shared=None):
		text=note_text(text);stamp=now()
		with self.transaction(shared)as db:
			for record_id in ids:identifier(record_id)
			for record_id in ids:
				row=db.execute(f"SELECT 1 FROM notes WHERE id = ? AND {VIOLATION_NOTES}",(record_id,)).fetchone()
				if row is not None:
					error=clause_ack_error(text,self.repository_root())
					if error:raise ValueError(error)
			for record_id in ids:
				for table in('notes','submissions'):
					row=db.execute(f"SELECT acknowledged_at, replies FROM {table} WHERE id = ?",(record_id,)).fetchone()
					if row is None:continue
					if row['acknowledged_at']is None:db.execute(f"UPDATE {table} SET acknowledged_at = ?, ack_type = ?, ack_text = ?, seen_at = COALESCE(seen_at, ?) WHERE id = ?",(stamp,'reply',text,stamp,record_id))
					else:more=replies_list(row['replies'])+[{'type':'reply','text':text,'at':stamp}];db.execute(f"UPDATE {table} SET replies = ?, ack_edited_at = ? WHERE id = ?",(json.dumps(more,ensure_ascii=False),stamp,record_id))
					if table=='submissions':db.execute('UPDATE reports SET agent_seen_at = ? WHERE id = (SELECT report_id FROM submissions WHERE id = ?)',(stamp,record_id))
					break
				else:raise ValueError(f"Unknown note: {record_id}; no receipts written")
			if not db.execute(f"SELECT 1 FROM notes WHERE {VIOLATION_NOTES} AND acknowledged_at IS NULL").fetchone():db.execute('DELETE FROM meta WHERE key = ?',(AGENT_INTERRUPT_FLAG_META,))
			db.execute("INSERT OR REPLACE INTO meta VALUES ('last_check', ?)",(stamp,))
			if not gate_pending_count(db):db.execute("INSERT OR REPLACE INTO meta VALUES (?, '0')",(POLLS_SINCE_MESSAGE,))
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
	def publish(self,report_id,title,source,submission_format=None):
		identifier(report_id)
		if not isinstance(title,str)or not title.strip()or len(title)>200:raise ValueError('Report title must contain 1–200 characters')
		source=Path(source)
		if source.suffix.lower()!='.md':raise ValueError('Publish a UTF-8 .md source file')
		with source.open('rb')as stream:data=stream.read(MAX_REPORT+1)
		if len(data)>MAX_REPORT:raise ValueError('Report exceeds the 2 MB limit; split it into reports')
		text=data.decode('utf-8');text=embed_report_images(text,source.resolve().parent);fields=parse_fields(text)[1];metadata=validate_submission_format(submission_format,fields)
		with self.transaction()as db:
			report=db.execute('SELECT ack_seen_at, viewed_at FROM reports WHERE id = ?',(report_id,)).fetchone()
			if report is not None:self.guard_report_change(db,report_id,report)
			else:
				answered=db.execute('SELECT 1 FROM submissions WHERE report_id = ? LIMIT 1',(report_id,)).fetchone()
				if answered is not None:raise ValueError(f"Report {report_id} has submitted answers; publish the update under a new ID")
			self.refuse_shared_id(db,'reports','tasks',report_id);highest=db.execute('SELECT COALESCE(MAX(seq), 0) FROM reports').fetchone()[0];db.execute('INSERT INTO reports (id, title, markdown, updated_at, published_at, seq, source, submission_format)\n           VALUES (?, ?, ?, ?, ?, ?, ?, ?)\n           ON CONFLICT(id) DO UPDATE SET title = excluded.title,\n             markdown = excluded.markdown, updated_at = excluded.updated_at,\n             source = excluded.source, submission_format = excluded.submission_format,\n             seq = COALESCE(reports.seq, excluded.seq), seen_at = NULL',(report_id,title,text,now(),now(),highest+1,str(source.resolve()),json.dumps(metadata)if metadata is not None else None))
		return len(fields)
	def unpublish(self,report_id,dismissed_by_owner=False,*,stale_only=False):
		identifier(report_id)
		with self.transaction()as db:
			if stale_only:db.execute('BEGIN IMMEDIATE')
			row=db.execute('SELECT title, ack_seen_at, viewed_at FROM reports WHERE id = ?',(report_id,)).fetchone()
			if row is None:
				if stale_only:return False
				raise FileNotFoundError('Report not found')
			if stale_only and report_id not in self.stale_report_ids(db):return False
			if not dismissed_by_owner:self.guard_report_change(db,report_id,row,hold_view=False,hold_answer=False)
			db.execute('DELETE FROM reports WHERE id = ?',(report_id,));self.release_report_tasks(db,report_id);db.execute('INSERT OR REPLACE INTO meta VALUES (?, ?)',(REMOVED_META,now()))
			if dismissed_by_owner:db.execute('INSERT INTO notes (id, text, at, quiet, blocking) VALUES (?, ?, ?, 1, 0)',(new_id(),f"The owner dismissed the report {report_id} ({row['title']}).",now()))
		if stale_only:return True
	def clear_state(self):
		guard_task_runs(self.path.parent,action='clear state');counts={}
		with self.transaction()as db:
			for table in('notes','reports','submissions','tasks','uploads','fetch_jobs'):counts[table]=db.execute(f"SELECT count(*) FROM {table}").fetchone()[0];db.execute(f"DELETE FROM {table}")
			kept=db.execute('SELECT key, value FROM meta WHERE key IN (?, ?)',(AGENT_KEY_META,'port')).fetchall();db.execute('DELETE FROM meta')
			for(key,value)in kept:db.execute('INSERT INTO meta VALUES (?, ?)',(key,value))
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
			stamp=now()if active else None;db.execute('UPDATE reports SET viewed_at = ?, last_viewed_at = COALESCE(?, last_viewed_at) WHERE id = ?',(stamp,stamp,report_id))
		return{'id':report_id,'viewed_at':clip_stamp(stamp)}
	def report(self,report_id,shared=None):
		identifier(report_id)
		with self.transaction(shared)as db:
			row=db.execute('SELECT * FROM reports WHERE id = ?',(report_id,)).fetchone()
			if row is None:raise FileNotFoundError('Report not found')
			return dict(row,submission_format=json.loads(row['submission_format'])if row['submission_format']else None)
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
			return self.submit(report_id,report['title'],fields,note_id,answers,shared=db,submission_format=report['submission_format'],revision=revision)
	def submit(self,report_id,title,fields,note_id,answers,shared=None,submission_format=None,revision=None):
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
		data={'revision':revision,'answers':answers}if revision is not None else None;return self.submission(note_id,report_id,'\n'.join(lines),shared=shared,answer_data=data)
def open_link(renderer,tokens,index,options,env):
	token=tokens[index];href=token.attrGet('href')or''
	if href and not href.startswith('#'):token.attrSet('target','_blank');token.attrSet('rel','noopener noreferrer')
	return renderer.renderToken(tokens,index,options,env)
def render_image(renderer,tokens,index,options,env):
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
		start,end=token.map;raw,cursor='\n'.join(lines[start:end]),0
		for child in token.children or():
			if child.type=='code_inline':ticks=re.compile('(?<!`)'+re.escape(child.markup)+'(?!`)');opening=ticks.search(raw,cursor);closing=ticks.search(raw,opening.end())if opening else None;cursor=closing.end()if closing else len(raw);continue
			if child.type in{'softbreak','hardbreak'}:
				newline=raw.find('\n',cursor)
				if newline>=0:cursor=newline+1
				continue
			content=child.markup if child.type=='text_special'else child.content
			if not content:continue
			offset=raw.find(content,cursor)
			if offset<0 and end-start>1:continue
			offset=max(0,offset);cursor=offset+len(content)
			if child.type!='text':continue
			for marker in('~~','=='):
				pattern=re.escape(marker)+'(.*?)'+re.escape(marker)
				for match in re.finditer(pattern,content):
					body=match.group(1)
					if body and(body[0].isspace()or body[-1].isspace()):line=start+1+raw.count('\n',0,offset+match.start());findings.add((line,marker))
	return[{'line':line,'marker':marker}for(line,marker)in sorted(findings)]
def render(markdown,breaks=False):
	try:from markdown_it import MarkdownIt;from markdown_it.common.normalize_url import validateLink
	except ImportError as error:raise RuntimeError("Markdown rendering needs markdown-it-py. Install it in the preview's venv and restart the server with that venv's Python; steering still works.")from error
	parser=MarkdownIt('commonmark',{'html':False,'breaks':breaks}).enable(['table','strikethrough']);highlight_markdown(parser);parser.add_render_rule('link_open',open_link);parser.add_render_rule('image',render_image);tagged,images=hold_out(markdown,SIZED_IMAGE,lambda match:sized_image(match,validateLink),'image');rendered=drop_paragraph_breaks(parser.render(unescape_fences(tagged)));return add_copy_buttons(put_back(rendered,images,'image'))
def handler(store):
	from http.server import BaseHTTPRequestHandler;questionnaire_state={'results':None,'answered_at':None,'requested':False};questionnaire_lock=threading.Lock()
	class Handler(BaseHTTPRequestHandler):
		def setup(self):super().setup();self.connection.settimeout(15)
		def reply(self,status,body,content_type='application/json; charset=utf-8',filename=None):
			data=body if isinstance(body,(bytes,bytearray))else body.encode('utf-8')
			if getattr(self,'ws_request_id',None)is not None:self.ws_send(1,json.dumps({'type':'response','id':self.ws_request_id,'status':status,'body':data.decode('utf-8'),'contentType':content_type}).encode());return
			self.send_response(status);self.send_header('Content-Type',content_type);self.send_header('Content-Length',str(len(data)));self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff');self.send_header('Content-Security-Policy',"default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src 'self' data: https:; connect-src 'self' https: ws: wss:; base-uri 'none'; form-action 'self'")
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
			for name in('Content-Type','X-Fetch-Claim','X-Fetch-Source'):
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
					version=db.execute('PRAGMA data_version').fetchone()[0];self.ws_send(1,b'{"type":"change"}')
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
						now=time.monotonic()
						if now-last_seen>WS_IDLE_SECONDS:self.ws_send(8,(1001).to_bytes(2,'big'));return
						if now-last_heartbeat>=WS_HEARTBEAT_SECONDS:self.ws_send(1,b'{"type":"heartbeat"}');last_heartbeat=now
			except(ValueError,TypeError)as error:
				code=1007 if isinstance(error,UnicodeDecodeError)else 1002
				try:self.ws_send(8,code.to_bytes(2,'big'))
				except OSError:pass
			except(OSError,sqlite_api().Error):return
		def do_GET(self):
			path=urlsplit(self.path).path
			try:
				if path=='/api/ws':self.websocket();return
				if path=='/':page=(ASSETS/'index.html').read_text(encoding='utf-8');page=page.replace('__STYLE__',(ASSETS/'style.css').read_text(encoding='utf-8'));page=page.replace('__MARKDOWN_SCRIPT__',(ASSETS/'markdown-it.min.js').read_text(encoding='utf-8'));page=page.replace('__SCRIPT__',(ASSETS/'message-markdown.js').read_text(encoding='utf-8')+'\n'+(ASSETS/'app.js').read_text(encoding='utf-8'));self.reply(200,page,'text/html; charset=utf-8');return
				if path=='/api/questionnaire':
					with questionnaire_lock:body=json.dumps({'results':questionnaire_state['results'],'answered_at':questionnaire_state['answered_at']})
					self.reply(200,body);return
				if path=='/api/userscript-autosave':self.reply(200,json.dumps(store.userscript_snapshot(),ensure_ascii=False));return
				if path=='/api/state':
					state=store.state()
					try:
						for item in state['notes']+[ack for report in state['reports']for ack in report['acknowledgements']]:
							if'text'in item:item['html']=render(item['text'])
							if item.get('ack_type')=='reply'and item.get('ack_text'):item['ack_html']=render(item['ack_text'].replace('\\n','\n'))
							for reply in item.get('replies')or[]:
								if reply['type']=='reply':reply['html']=render(reply['text'].replace('\\n','\n'))
					except RuntimeError as error:state['rendering_error']=str(error)
					self.reply(200,json.dumps(state,ensure_ascii=False));return
				if path=='/api/submissions':live={report['id']for report in store.state()['reports']};self.reply(200,json.dumps([saved_answer_line(record)for record in store.submissions()if record['report_id']in live],ensure_ascii=False),'application/json; charset=utf-8');return
				if path=='/api/copy-state':notes=store.state()['notes'];lines,counts=store.state_lines(notes,store.tasks()or{});stamps=store.newest_stamps();self.reply(200,json.dumps({'text':state_ndjson(lines),'counts':counts,'stamp':store.newest_stamp(stamps),'stamps':{'note':stamps['note'],'task':stamps['task']},'repo':store.path.parent.parent.name,'branch':workspace_branch(store.path.parent.parent)},ensure_ascii=False),'application/json; charset=utf-8');return
				if path=='/api/report-sources':self.reply(200,json.dumps([saved_report_line(record)for record in store.report_sources()],ensure_ascii=False),'application/json; charset=utf-8');return
				match=re.fullmatch('/api/reports/([a-zA-Z0-9_-]{1,80})/(html|source)',path)
				if match:
					report_id,type=match.groups();report=store.report(report_id)
					if type=='html':
						try:body,questions=render_report(report['markdown'])
						except ValueError as error:self.problem(500,f"This report cannot be rendered: {error}");return
						self.reply(200,json.dumps({'html':body,'fields':len(questions),'warnings':report_marker_warnings(report['markdown']),'revision':report['updated_at'],'published':report['published_at']or report['updated_at'],'edited':report['updated_at']},ensure_ascii=False));return
					self.reply(200,report['markdown'],'text/plain; charset=utf-8',f"{report_id}.md");return
				self.problem(404,'Not found')
			except FileNotFoundError as error:self.problem(404,error)
			except(OSError,sqlite_api().Error,RuntimeError)as error:self.problem(503,error)
		def do_POST(self):
			path=urlsplit(self.path).path;autosave_post=path in{'/api/userscript-autosave','/api/userscript-autosave/compare'};report_submit=re.fullmatch('/api/reports/([a-zA-Z0-9_-]{1,80})/submit',path);report_seen=re.fullmatch('/api/reports/([a-zA-Z0-9_-]{1,80})/seen',path);report_ack_seen=re.fullmatch('/api/reports/([a-zA-Z0-9_-]{1,80})/ack-seen',path);report_view=re.fullmatch('/api/reports/([a-zA-Z0-9_-]{1,80})/view',path);message_replies_seen=re.fullmatch('/api/messages/([a-zA-Z0-9_-]{1,80})/replies/seen',path);report_unpublish=re.fullmatch('/api/reports/([a-zA-Z0-9_-]{1,80})/unpublish',path);fetch_post=re.fullmatch('/api/fetch-jobs/([a-zA-Z0-9_-]{1,80})/(renew|result|fail|retry|approve|deny|drop)',path);note_upload=path=='/api/notes/with-file';staged_upload=path=='/api/uploads';upload_drop=re.fullmatch('/api/uploads/([a-zA-Z0-9_-]{1,80})/drop',path);agent_key_post=path=='/api/key';skip_poll_post=path=='/api/skip-poll';interrupt_post=path=='/api/agent-interrupt';task_order_post=path=='/api/tasks/order';fetch_result=bool(fetch_post and fetch_post.group(2)=='result')
			if path not in{'/api/notes','/api/markdown','/api/fetch-jobs','/api/fetch-jobs/claim','/api/questionnaire','/api/questionnaire/request'}and not report_submit and not report_seen and not report_ack_seen and not report_view and not message_replies_seen and not report_unpublish and not note_upload and not staged_upload and not upload_drop and not fetch_post and not agent_key_post and not skip_poll_post and not interrupt_post and not task_order_post and not autosave_post:self.problem(404,'Not found');return
			content_type=self.headers.get('Content-Type','')
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
				if path=='/api/questionnaire':
					import questionnaire;results=questionnaire.grade(data.decode('utf-8'))
					with questionnaire_lock:questionnaire_state['results']=results;questionnaire_state['answered_at']=now();questionnaire_state['requested']=False
					self.reply(200,json.dumps({'ok':True}));return
				payload=json.loads(data)
				if not isinstance(payload,dict):self.problem(400,'Expected a JSON object');return
				if path=='/api/questionnaire/request':
					if payload:raise ValueError('Questionnaire request requires an empty object')
					with questionnaire_lock:
						if not questionnaire_state['requested']:
							with closing(store.connect())as db:pending=db.execute('SELECT id FROM notes WHERE type = ? AND acknowledged_at IS NULL LIMIT 1',('questionnaire',)).fetchone()
							if pending is None:store.note(uuid.uuid4().hex[:6],'Acknowledge this note, then follow the arena-skill questionnaire procedure.',quiet=True,blocking=True,type='questionnaire')
							questionnaire_state['requested']=True
					self.reply(200,json.dumps({'ok':True}));return
				if autosave_post:result=store.compare_userscript_save(payload)if path.endswith('/compare')else store.confirm_userscript_save(payload);self.reply(200,json.dumps(result,ensure_ascii=False));return
				if agent_key_post:
					candidate=payload.get('key')
					if not isinstance(candidate,str)or not AGENT_KEY_RE.fullmatch(candidate):raise ValueError('key must be 20 to 64 letters, digits, dashes or underscores')
					host=payload.get('host')
					if host is not None and(not isinstance(host,str)or not AGENT_HOST_RE.fullmatch(host)):raise ValueError('host must be one https origin, with no path')
					store.set_agent_key(candidate,host);self.reply(200,json.dumps(store.agent_key(),ensure_ascii=False));return
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
				if payload.get('upload_ids')is not None:note=store.note_with_staged(payload.get('id'),payload.get('text'),payload.get('upload_ids'))
				else:note=store.note(payload.get('id'),payload.get('text'),quiet=bool(payload.get('quiet')))
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
def install_portable_parsers(tool_dir=None,which=shutil.which,runner=subprocess.run):
	target=parser_tool_dir(tool_dir);target.mkdir(parents=True,exist_ok=True);manifest_path=target/'package.json';lockfile_path=target/'package-lock.json';manifest_text=json.dumps(PARSER_MANIFEST,indent=2)+'\n';lockfile_text=json.dumps(PARSER_LOCKFILE,indent=2)+'\n'
	if not manifest_path.is_file()or manifest_path.read_text(encoding='utf-8')!=manifest_text:manifest_path.write_text(manifest_text,encoding='utf-8')
	if not lockfile_path.is_file()or lockfile_path.read_text(encoding='utf-8')!=lockfile_text:lockfile_path.write_text(lockfile_text,encoding='utf-8')
	existing=discover_portable_parsers(target)
	if existing['available']:return{**existing,'status':'reused'}
	if not which('node')or not which('npm'):return{**existing,'status':'skipped','message':'Node or npm is unavailable; skipping portable parser install (preview remains usable).'}
	completed=runner(['npm','ci','--ignore-scripts','--no-audit','--no-fund'],cwd=str(target),capture_output=True,text=True,check=False);after=discover_portable_parsers(target)
	if completed.returncode!=0 or not after['available']:detail=(completed.stderr or completed.stdout or'npm ci failed').strip();return{**after,'status':'failed','message':f"Portable parser install failed ({detail}); preview remains usable."}
	return{**after,'status':'installed'}
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
def embed_report_images(markdown,report_dir):
	def swap(match):
		alt,destination,suffix=match.groups();source=destination[1:-1]if destination.startswith('<')else destination
		if source.startswith('data:'):return match.group(0)
		if source.startswith('//')or re.match('[a-zA-Z][a-zA-Z0-9+.-]*:',source):return f"Publication skips remote image {source}. Fetch it locally, then embed it with arena-preview image-data-uri."
		path=Path(source)
		if not path.is_absolute():path=report_dir/path
		try:mime,encoded=image_payload(path)
		except ValueError as error:return f"Publication skips local image {source}: {error}. Run arena-preview image-data-uri for the helper."
		return f"![{alt}](data:{mime};base64,{encoded}{suffix})"
	return IMAGE_IN_MARKDOWN.sub(swap,markdown)
def gate_verdict(state_dir,push,line,out=None):
	out=sys.stdout if out is None else out;run_notices(state_dir,out=out)
	if poll_timeout_line(line):print('TIMEOUT BANNED: never wrap `arena-preview poll` in shell `timeout`. Set the Bash tool timeout to 1800 seconds; the poll returns within its 1799-second wait.',file=out,flush=True);return 1
	try:gate_store=Store(state_dir);allowed=gate_store.gate(pending_only=push);blocked_calls=gate_store.calls_since_message()if not allowed and not push else None
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
		return 1
	if push and main_identical():print('HEAD equals `origin/main`, so the push carries nothing. Start new work from `origin/main`.',file=out,flush=True);return 1
	stale=main_ancestor_warning()if push else''
	if stale:print(stale,file=out,flush=True);return 1
	return 0
PRE_STEPS='interrupt','read-set','remind','main-local','main-fresh','bare','proxy','gate'
GATED_SHELLS=frozenset({'bash','sh','dash','ksh','zsh'})
PLATFORM_LINES='arena-workspace','ss -ltn','netstat -ltn','source ~/.profile',"mkdir -p '/home/user'"
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
	warning=idle_wait(line)
	if warning:report(warning);return finish(130)
	status=pr_check_wait(state_dir,line,command)
	if status==PR_CHECK_RUNS:return finish(2)
	if status==PR_CHECK_REFUSED:return finish(130)
	if'bare'not in finished and bare_script_invocation(line):mark('bare');report('Use bare arena-preview from any directory. Drop full path.')
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
	if'git commit'in command:
		warning=worktree_warning(state_dir,'commit',cwd=Path.cwd())
		if warning:report(warning);return finish(130)
		try:commit_store=Store(state_dir)
		except FileNotFoundError:commit_store=None
		if commit_store is not None:
			found=commit_id_report(staged_session_id_hits(commit_store,str(Path.cwd())))
			if found:report(found);return finish(130)
	if any(word in command for word in('preview','profile','bashrc','arena-state','git commit','--help'))or command.startswith(('mkdir','[','gh','sleep','true',':','test','git status','git diff','git add'))or any(word in command for word in('arena-workspace','ss -ltn','netstat -ltn')):return finish(0)
	if'git push'in command:
		warning=worktree_warning(state_dir,'push')
		if warning:report(warning);return finish(130)
		if gate_verdict(state_dir,True,line,out=sys.stderr)==1:return finish(130)
	if quiet_inbox_line(line):return finish(0)
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
class PreviewArgumentParser(argparse.ArgumentParser):
	def print_usage(self,file=None):pass
	def exit(self,status=0,message=None):super().exit(status,message.removeprefix('arena-preview: ')if message else message)
def main():
	parser=PreviewArgumentParser(description=CLI_DESCRIPTION,prog='arena-preview');parser.add_argument('-r','--reminder',action='store_true',help='Print unacked-count reminder; exit');commands=parser.add_subparsers(dest='command',required=False);serve=commands.add_parser('serve',help='Serve preview page; -p names port');serve.add_argument('-p','--port',type=int,default=DEFAULT_PORT,help=f"Port (default: {DEFAULT_PORT}; only supported port)");init=commands.add_parser('init',help='Create state directory and database');init.add_argument('--tz',help='CLI stamp IANA zone; TZ env wins');questionnaire_parser=commands.add_parser('questionnaire',help='Show questions and schema, or submit ordered JSON');questionnaire_parser.add_argument('answers',nargs='?',help='Ordered answer JSON');commands.add_parser('read',help='Print every pending note, answer and upload');commands.add_parser('skills',help='List skill names, full descriptions, and paths without loading skills');note_group=commands.add_parser('note',help='Read stored notes');note_verbs=note_group.add_subparsers(dest='verb',required=True);note_list=note_verbs.add_parser('list',help='Stored note IDs by page, newest first; acknowledged included');note_list.add_argument('--page',type=int,default=1,help='Page number (default: 1)');note_list.add_argument('--page-size',type=int,default=20,help='IDs per page, 1 to 100 (default: 20)');note_reread=note_verbs.add_parser('show',help='Reread stored note by short or full ID');note_reread.add_argument('id',help='Six or all note ID characters');image_uri=commands.add_parser('image-data-uri',help='Print Markdown: one local image as data URI');image_uri.add_argument('path',type=Path,help='One local PNG, JPEG, GIF or WebP file up to 2 MiB');outline_parser=commands.add_parser('outline',help='Print read-only source outline with line ranges for local repository files');outline_parser.add_argument('path',type=Path,help='Local source file inside the repository checkout');outline_parser.add_argument('--max-entries',type=int,default=OUTLINE_MAX_ENTRIES,help=f"Maximum entries to print, 1 to {OUTLINE_MAX_ENTRIES} (default: {OUTLINE_MAX_ENTRIES})");outline_parser.add_argument('--json',dest='as_json',action='store_true',help='Print JSON outline with path, language, entries, total, and omitted count');commands.add_parser('key',help='Print the recorded agent key, host and use line');pre=commands.add_parser('pre',help='Run all pre-Bash gates in order; list completed steps');pre.add_argument('--line',default='',help='Shell command line from hook');pre.add_argument('--command',dest='command_text',default='',help='Command reached by DEBUG trap');pre.add_argument('--repo',default='.',help='Bible repository');pre.add_argument('--shell',default='',help='Shell name');pre.add_argument('--done',default='',help="This shell's completed steps, space-separated");run_group=commands.add_parser('run',help='Start long command; read result');run_verbs=run_group.add_subparsers(dest='verb',required=True);route=run_verbs.add_parser('start',help='Detach command, keep output tail; show exit on later call');route.add_argument('--task',dest='task_id',metavar='TASK-ID',help='Link run to upcoming task, not current task');route.add_argument('--after',metavar='GATE',help='Run only after this gate passes');route.add_argument('name',help='Short run name; example: gates');route.add_argument('command_args',nargs=argparse.REMAINDER,help='Command after --');route_run=commands.add_parser('route-run',help='Run command here; record exit for later call');route_run.add_argument('name',help='Run name already recorded by caller');route_run.add_argument('command_args',nargs=argparse.REMAINDER,help='Command after --');logs=run_verbs.add_parser('logs',help='Print routed run status and recorded output tail');logs.add_argument('name',help='Run name to read');worktree=commands.add_parser('worktree',help='Create or collect detached task worktree beside root gate');worktree_action=worktree.add_subparsers(dest='worktree_action',required=True);worktree_start=worktree_action.add_parser('start',help='Create detached worktree beside root gate, or without one');worktree_start.add_argument('name',help='Short worktree name');worktree_start.add_argument('destination',type=Path,help='New path outside root');worktree_start.add_argument('--gate',default=None,help='Running root gate name; optional');worktree_start.add_argument('--allow',action='append',default=[],help='Glob of paths task may change');worktree_collect=worktree_action.add_parser('collect',help='Print allowed detached-worktree patch after root gate passes');worktree_collect.add_argument('name',help='Worktree name to collect');rename=commands.add_parser('rename',help='Move note, answer, report, or task to new ID');rename.add_argument('old_id',help='Stored ID or unique record prefix');rename.add_argument('new_id',help='Destination record ID');gate=commands.add_parser('gate',help='Inbox gate; -p blocks on any unacked note; -l names blocked line');gate.add_argument('-p','--push',action='store_true',help='Block on any unacked note or answer, regardless of call count');gate.add_argument('-l','--line',default='',help='Command line from hook; blocked calls name commands to drop');commands.add_parser('poll',help='Wait for owner answer or gate completion');download=commands.add_parser('download-request',help='Request HTTPS browser download; preview Approve click required; -a allows proxy fallback');download.add_argument('url',help='One HTTPS URL without embedded credentials');download.add_argument('-a','--allow-proxy',action='store_true',help='Owner may opt in to AllOrigins and CodeTabs fallback for this request');ack=commands.add_parser('ack',help='Answer notes with inline Markdown; omit IDs for one pending item');ack.add_argument('ids',nargs='*');ack.add_argument('markdown',help='Literal inline Markdown answer');ack.add_argument('--id',dest='task_id');ack.add_argument('--title',dest='task_title');ack.add_argument('--type',dest='task_type',choices=TASK_TYPES);ack.add_argument('--detail',dest='task_detail',action='append');report_group=commands.add_parser('report',help='Publish, withdraw and inspect reports');report_verbs=report_group.add_subparsers(dest='verb',required=True);publish=report_verbs.add_parser('publish',help='Publish report; -i ID, -t title');publish.add_argument('source',type=Path);publish.add_argument('--submission-format',type=Path,help='Optional question, answer, and template JSON');publish.add_argument('-i','--id',required=True);publish.add_argument('-t','--title',required=True);unpublish=report_verbs.add_parser('unpublish',help='Withdraw report by ID, or all stale reports with --stale');unpublish.add_argument('report_id',nargs='?');unpublish.add_argument('--stale',action='store_true',help='Withdraw every stale report');report_verbs.add_parser('stale',help='List stale published report IDs and reasons without changing state')
	for verb in('pin','unpin'):pin=report_verbs.add_parser(verb,help=f"{verb.capitalize()} a report against staleness");pin.add_argument('report_id')
	report_state=report_verbs.add_parser('state',help='Print publication, owner view stamp and answer visibility for one report or all');report_state.add_argument('report_id',nargs='?');task_group=commands.add_parser('task',help='Read and update tasks');task_verbs=task_group.add_subparsers(dest='verb',required=True);task_list=task_verbs.add_parser('list',help='List tasks; -f includes finished');task_list.add_argument('-f','--full',action='store_true',help='print every task, finished included');task_show=task_verbs.add_parser('show',help='Print one stored task in full');task_show.add_argument('task_id',metavar='TASK-ID');task=task_verbs.add_parser('set',help='Write/update task; -s status, -o order, -d details, -A append, -m message, -r report, -u unblocked, -a amend, --type type');task.add_argument('id_arg',nargs='?',metavar='TASK-ID');task.add_argument('title_arg',nargs='?',metavar='TASK-TITLE');task.add_argument('detail_arg',nargs='*',metavar='TASK-DETAIL');task.add_argument('-d','--details',dest='task_details',action='append',help='One step per line; max 120 characters; repeatable');task.add_argument('-A','--append',dest='append_details',action='store_true',help='Append provided details instead of replacing stored details');task.add_argument('-u','--unblocked',dest='blocked',action='store_false',default=None,help='Clear report wait or legacy blocked mark');task.add_argument('-m','--msg-id',help='Message task answers; mark it as having a task');task.add_argument('-r','--report',metavar='REPORT-ID',help='Awaited report; answer clears blocked mark');task.add_argument('-a','--amend',metavar='PREV-ID',help='Rename task under this ID to given ID');task.add_argument('-b','--blocked-by',dest='blocked_by',action='append',metavar='TASK-ID',help='Dependency task; repeatable; cycles refused');task.add_argument('-s','--status',choices=TASK_STATUSES,default=None);task.add_argument('--type',dest='task_type',choices=TASK_TYPES,default=None,help='Primary deliverable type; omitted flag keeps stored type');task.add_argument('-o','--order',type=int,default=None,help='1-based place within div, not end');task_unlink=task_verbs.add_parser('unlink',help='Unlink a submission from all tasks; retain its history');task_unlink.add_argument('submission_id');task_remove=task_verbs.add_parser('remove',help='Delete task by ID');task_remove.add_argument('task_id');state_group=commands.add_parser('state',help='Import or clear stored state');state_verbs=state_group.add_subparsers(dest='verb',required=True);state_import=state_verbs.add_parser('import',help='Import NDJSON backup; replace live state');state_import.add_argument('source',nargs='?',type=Path);state_import.add_argument('-r','--replace-tasks',action='store_true');state_verbs.add_parser('clear',help='Empty all state tables in place; keep agent key record');args=parser.parse_args()
	if args.command=='serve'and args.port!=DEFAULT_PORT:serve.error(f"--port must be {DEFAULT_PORT}")
	verb=getattr(args,'verb',None);grouped_commands={('note','list'):'note-list',('note','show'):'note',('run','start'):'route',('run','logs'):'logs',('report','publish'):'publish',('report','unpublish'):'unpublish',('report','stale'):'stale-reports',('report','state'):'report-state',('report','pin'):'report-pin',('report','unpin'):'report-unpin',('task','list'):'task-list',('task','show'):'task-show',('task','set'):'task',('task','remove'):'task-remove',('task','unlink'):'task-unlink',('state','import'):'import-state',('state','clear'):'clear-state'};args.command=grouped_commands.get((args.command,verb),args.command)
	if args.command=='route':
		command_args=args.command_args;separator=command_args.index('--')if'--'in command_args else len(command_args)
		if any(argument==option or argument.startswith(option+'=')for argument in command_args[:separator]for option in('--task','--after')):parser.error('put --task and --after before the run name. Use: arena-preview run start --task <id> <name> -- <command>')
	if args.command=='unpublish'and args.stale:
		if args.report_id:parser.error('give a report ID or --stale, not both')
		args.command='report'
	state_dir=resolve_state_dir();fresh_state=not(Path(state_dir).resolve()/'state.sqlite3').is_file()
	try:
		if args.reminder:
			store=Store(state_dir,create=False);require_server(store);reminder=store.reminder(advance=True);store.set_meta(AGENT_CALL_ENDED_META,now())
			if'unacked 'in reminder or TASK_ORDER_NOTICE in reminder or not run_notices(state_dir,show_running=True):print(reminder,file=sys.stderr,flush=True)
			return 0
		if not args.command:parser.error('Command required. Run arena-preview --help.')
		if args.command=='pre':return pre_command(state_dir,args.line,args.command_text,args.repo,done=args.done,shell=args.shell)
		if args.command=='questionnaire':return questionnaire_command(state_dir,args.answers)
		if args.command=='skills':print_skills();return 0
		if args.command=='image-data-uri':
			try:print(image_data_uri(args.path))
			except ValueError as error:print(error,file=sys.stderr);return 1
			return 0
		if args.command=='outline':
			try:result=outline_file(args.path,max_entries=args.max_entries);print(format_outline(result,as_json=args.as_json))
			except ValueError as error:print(error,file=sys.stderr);return 1
			return 0
		if args.command=='worktree':
			if args.worktree_action=='start':record=start_worktree(state_dir,args.name,args.destination,args.gate,args.allow);print(f"Worktree {record['name']} at {record['worktree']}");return 0
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
		store=Store(state_dir,create=args.command in{'serve','init','import-state'});global DISPLAY_ZONE;DISPLAY_ZONE=resolve_display_zone(store)
		if args.command!='poll':clear_poll_repeat(store)
		if args.command not in{'serve','init'}:require_server(store)
		read_only=args.command in{'stale-reports','note-list','report-state'}
		if not read_only:
			routine=os.environ.get('ARENA_PREVIEW_SHELL_HOOK')!='1'or os.environ.get('ARENA_PREVIEW_ROUTE')=='1';reminder=store.reminder(routine=routine)
			if reminder:print(reminder,file=sys.stderr,flush=True)
		if args.command!='serve'and not read_only:store.touch_agent()
		if args.command=='serve':
			from http.server import ThreadingHTTPServer;require_renderer()
			with ThreadingHTTPServer(('0.0.0.0',args.port),handler(store))as server:store.set_meta('port',str(server.server_port));threading.Thread(target=watch_call_group,args=(store,),daemon=True).start();print(f"Preview on 0.0.0.0:{server.server_port}; state: {store.path}",flush=True);server.serve_forever()
		elif args.command=='read':require_server(store);store.notice_expired_key();print_read(store)
		elif args.command=='note-list':
			ids,total=store.list_note_ids(args.page,args.page_size);pages=max(1,(total+args.page_size-1)//args.page_size)
			if args.page>pages:print(f"Page {args.page} is past the last page ({pages}).",file=sys.stderr);return 1
			print(f"Notes page {args.page}/{pages} ({total} total; newest first)")
			for note_id in ids:print(note_id)
		elif args.command=='note':
			rows=store.notes_matching(args.id)
			if not rows:print(f"No note matches {args.id}.",file=sys.stderr);return 1
			for row in rows:head=f"note {row['id']} at {local_stamp(row['at'])}";head+=f" acked {local_stamp(row['acknowledged_at'])}"if row['acknowledged_at']else' unacked';print(head);print(row['text']);print()
		elif args.command=='key':
			record=store.agent_key()
			if not record:print('No agent key recorded.',file=sys.stderr);return 1
			print(format_key(record))
		elif args.command=='poll':
			require_server(store);store.notice_expired_key();refusal=poll_repeat(store)
			if refusal:print(refusal,flush=True);return 2
			return poll_once(store)
		elif args.command=='download-request':print(format_fetch_job(store.enqueue_fetch(args.url,args.allow_proxy,pending=True)))
		elif args.command=='ack':
			if args.markdown=='-':raise ValueError('Pass literal inline Markdown; acknowledgement stdin is not supported')
			text=args.markdown;ids=[store.resolve_id(token)for token in args.ids]if args.ids else[store.pending_ack()];task_options=args.task_id,args.task_title,args.task_type,args.task_detail
			if any(value is not None for value in task_options):
				if len(args.ids)!=1 or not all(task_options):raise ValueError('A combined acknowledgement needs one explicit message ID and all four task options')
				details=task_steps(args.task_detail);check_task_steps(details)
				with store.transaction()as shared:store.write_task(args.task_id,args.task_title,details,task_type=args.task_type,shared=shared);store.acknowledge(ids,text,shared=shared);store.mark_task(ids[0],args.task_id,shared=shared)
			else:store.acknowledge(ids,text)
			short=[i[:6]for i in ids];print('ok '+' '.join(short));untracked=[record_id for record_id in ids if not any(record.get('task_id')is not None for(_,record)in store.id_matches(record_id)if record['id']==record_id)]
			if untracked:print('link it with: '+'; '.join(f'arena-preview task set <id> "<title>" -m {i}'for i in untracked))
		elif args.command=='publish':
			metadata=None
			if args.submission_format is not None:
				with args.submission_format.open('rb')as stream:data=stream.read(MAX_REPORT+1)
				if len(data)>MAX_REPORT:raise ValueError('Submission format exceeds the report size limit')
				metadata=json.loads(data.decode('utf-8'))
			count=store.publish(args.id,args.title,args.source,submission_format=metadata);revision=store.report(args.id)['updated_at'];warnings=verify_publication(store,args.id,revision,count);plural=''if count==1 else's';print(f"Published {args.id}, {count} field{plural}. Verified.");recommendations=[f"{question['prompt']}: {', '.join(question['default'])}"for question in parse_fields(Path(args.source).read_text('utf-8'))[1]if question.get('default')]
			if recommendations:print('you recommended:\n'+'\n'.join(recommendations))
			for warning in warnings or():print(f"{args.source}:{warning['line']}: Warning: possible unparsed {warning['marker']} marker. Keep spaces outside the markers. Literal marker text can be intentional.",file=sys.stderr)
			if not count and'{#'in Path(args.source).read_text('utf-8'):print('Warning: 0 fields parsed. A `{#id}` marker ends a prompt line, then the `- ( ) option` lines follow.',file=sys.stderr)
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
			if args.title_arg is None and not args.detail_arg and args.task_details is None and not args.append_details and args.status is None and args.order is None and args.blocked is None and args.report is None and args.msg_id is None and args.amend is None and args.task_type is None and args.blocked_by is None:print_task_row(store,task_id);return 0
			details=args.task_details
			if details is None and args.detail_arg:details=args.detail_arg
			if details is not None:details=task_steps(details);check_task_steps(details)
			if args.amend:store.amend_task(store.resolve_id(args.amend),task_id)
			if args.report:
				try:store.report(args.report)
				except FileNotFoundError:raise ValueError(f"No report is stored under {args.report}")from None
				if args.blocked is None:args.blocked=True
			if args.msg_id:
				with store.transaction()as shared:record=store.write_task(task_id,args.title_arg,details,args.status,args.order,args.blocked,args.report,task_type=args.task_type,blocked_by=args.blocked_by,shared=shared,append_details=args.append_details);record=store.mark_task(args.msg_id,task_id,shared=shared)or record
			else:record=store.write_task(task_id,args.title_arg,details,args.status,args.order,args.blocked,args.report,task_type=args.task_type,blocked_by=args.blocked_by,append_details=args.append_details)
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
		elif args.command=='clear-state':print(format_clear_state(store.clear_state()))
		elif args.command=='init':
			if args.tz:
				if not zone_exists(args.tz):print(f"Unknown zone {args.tz}.",file=sys.stderr);return 1
				store.set_meta(TIMEZONE_META,args.tz);DISPLAY_ZONE=resolve_display_zone(store)
			print(f"state {store.path} {'created'if fresh_state else'present'}")
	except(OSError,ValueError,TypeError,KeyError,sqlite_api().Error,RuntimeError)as error:print(f"Preview error: {error}",file=sys.stderr);return 1
	return 0
if __name__=='__main__':ensure_installed();raise SystemExit(main())
