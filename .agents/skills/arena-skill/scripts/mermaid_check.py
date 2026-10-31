import re
ALLOWED_HEADERS=frozenset({'flowchart','graph','sequenceDiagram','stateDiagram','stateDiagram-v2','classDiagram','erDiagram','gantt','pie','gitGraph','journey','mindmap','timeline','quadrantChart'})
BLOCK_OPENERS=frozenset({'subgraph','rect','alt','loop','opt','par'})
FENCE_RE=re.compile('^ {0,3}(`{3,}|~{3,})[ \\t]*([^ \\t]+)?')
HEADER_RE=re.compile('[A-Za-z][A-Za-z0-9-]*')
DANGLING_ARROW_RE=re.compile('(?:-\\.->|-->>|->>|-->|==>|---|===|-\\.-|->|--x|--o|--)(?:\\|[^|]*\\|)?$')
BRACKET_PAIRS={'[':']','(':')','{':'}'}
BRACKET_CLOSERS={closer:opener for(opener,closer)in BRACKET_PAIRS.items()}
def _closes_fence(line:str,fence:str)->bool:stripped=line.strip();return len(stripped)>=len(fence)and set(stripped)=={fence[0]}
def _fenced_mermaid_blocks(markdown_text:str)->list[tuple[int,list[tuple[int,str]]]]:
	blocks:list[tuple[int,list[tuple[int,str]]]]=[];fence:str|None=None;start=0;lines:list[tuple[int,str]]|None=None
	for(number,line)in enumerate(markdown_text.splitlines(),start=1):
		if fence is not None:
			if _closes_fence(line,fence):
				if lines is not None:blocks.append((start,lines))
				fence,lines=None,None
			elif lines is not None:lines.append((number,line))
			continue
		opening=FENCE_RE.match(line)
		if opening:
			fence=opening.group(1)
			if opening.group(2)=='mermaid':start,lines=number,[]
	if lines is not None:blocks.append((start,lines))
	return blocks
def _strip_quoted(meaningful:list[tuple[int,str]])->tuple[list[tuple[int,str]],int|None]:
	cleaned:list[tuple[int,str]]=[];quote_line:int|None=None
	for(number,text)in meaningful:
		chars:list[str]=[];escaped=False
		for char in text:
			if escaped:
				escaped=False
				if quote_line is None:chars.append(char)
				continue
			if char=='\\'and quote_line is not None:escaped=True;continue
			if char=='"':quote_line=number if quote_line is None else None;continue
			if quote_line is None:chars.append(char)
		cleaned.append((number,''.join(chars).strip()))
	return cleaned,quote_line
def _without_er_cardinality(text:str)->str:return re.sub('[|o{}]{2,}',lambda match:match.group(0).replace('{',' ').replace('}',' '),text)
def _bracket_issues(cleaned:list[tuple[int,str]],diagram:str)->list[tuple[int,str]]:
	balances={opener:0 for opener in BRACKET_PAIRS};surplus_lines:dict[str,int]={}
	for(number,text)in cleaned:
		if diagram=='erDiagram':text=_without_er_cardinality(text)
		for char in text:
			if char in BRACKET_PAIRS:
				if balances[char]==0:surplus_lines[char]=number
				balances[char]+=1
			elif char in BRACKET_CLOSERS:
				opener=BRACKET_CLOSERS[char];balances[opener]-=1
				if balances[opener]<=0:surplus_lines.pop(opener,None)
	return[(surplus_lines[opener],f"unclosed '{opener}' bracket")for(opener,balance)in balances.items()if balance>0]
def _check_block(start:int,lines:list[tuple[int,str]])->list[tuple[int,str]]:
	meaningful=[(number,text.strip())for(number,text)in lines if text.strip()and not text.strip().startswith('%%')]
	if not meaningful:return[(start,'empty mermaid block')]
	header_line,header_text=meaningful[0];match=HEADER_RE.match(header_text);header=match.group(0)if match else header_text.split(maxsplit=1)[0]
	if header not in ALLOWED_HEADERS:return[(header_line,f"unknown diagram header '{header}'")]
	issues:list[tuple[int,str]]=[];cleaned,quote_line=_strip_quoted(meaningful)
	if quote_line is not None:issues.append((quote_line,'unclosed quote'))
	else:issues.extend(_bracket_issues(cleaned,header))
	open_blocks:list[tuple[str,int]]=[]
	for(number,text)in meaningful:
		first=text.split(maxsplit=1)[0].rstrip(';')
		if first in BLOCK_OPENERS:open_blocks.append((first,number))
		elif first=='end'and open_blocks:open_blocks.pop()
	issues.extend((number,f"unclosed '{keyword}' block")for(keyword,number)in open_blocks)
	for(number,text)in cleaned:
		if DANGLING_ARROW_RE.search(text.rstrip(';').rstrip()):issues.append((number,'dangling arrow operator'))
	issues.sort(key=lambda issue:issue[0]);return issues
def check_mermaid_markdown(markdown_text:str)->list[tuple[int,str]]:
	issues:list[tuple[int,str]]=[]
	for(start,lines)in _fenced_mermaid_blocks(markdown_text):issues.extend(_check_block(start,lines))
	return issues
def format_mermaid_warnings(source_label:str,issues:list[tuple[int,str]])->list[str]:return[f"Mermaid warning ({source_label}:{line}): {reason}"for(line,reason)in issues]
