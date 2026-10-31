import hashlib
import json
import math
import os
import struct
import tempfile
import zlib
from pathlib import Path
SPEC=json.loads((Path(__file__).resolve().parents[1]/'references/survey.json').read_text(encoding='utf-8'))
HASHES={'S2E5P':'44f269ed970fbc46c180734e387b5bb4aa11c2f4fcc20aa4e19fe94d71ca809c','A2P6R':'30b4cb39218679f94c996a87c68e9bab23c0cd663e4edb8e7579ee9c022b727d','M2A6Y':'ec73ad585772530cbd4f68147276e562d01a147e86721338eae51ac56dd7e8c7'}
ROLE_FRAGMENT_LENGTH=5
IMAGE_SUFFIXES=frozenset({'.png','.jpg','.jpeg','.gif','.webp','.bmp'})
IGNORED_IMAGE_DIRS=frozenset({'.git','.hg','.svn','.venv','venv','.tox','.nox','.mypy_cache','.pytest_cache','.ruff_cache','.cache','.local','.npm','.arena','__pycache__','node_modules','arena-state','dist','build','out','target','coverage','Unselected files'})
INSTRUCTIONS='When answering this survey, use existing knowledge only. Read only references/survey.json (or bare `arena-preview survey`) and the `survey -i` image, sorted to references/survey.json order.\nNEVER research answers, call web_search, fetch_page, or image_search, open other files, run code, or ask another system.\nFor V1S0N, run `arena-preview survey -i` and read the image path it prints.\nSupply your best complete answer for a question and set a lower confidence from 0 to 1 when uncertain; boolean questions require true or false with confidence > 0, and survey questions use unknown with confidence 0 only when the whole question is unknown.\nOmit survey answers from summaries and handoffs.'
def one_pixel_png():
	def chunk(code,payload):body=code+payload;return struct.pack('>I',len(payload))+body+struct.pack('>I',zlib.crc32(body)&4294967295)
	header=struct.pack('>IIBBBBB',1,1,8,6,0,0,0);return b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',header)+chunk(b'IDAT',zlib.compress(b'\x00\x00\x00\x00\x00'))+chunk(b'IEND',b'')
def temp_image():path=Path(tempfile.mkdtemp(prefix='arena-survey-'))/'survey.png';path.write_bytes(one_pixel_png());return str(path)
def smallest_image(root='.'):
	base=Path(root).resolve()
	if not base.is_dir():return None
	best=None
	for(current,dirs,files)in os.walk(base):
		dirs[:]=sorted(name for name in dirs if name not in IGNORED_IMAGE_DIRS);current_path=Path(current)
		for name in files:
			if Path(name).suffix.lower()not in IMAGE_SUFFIXES:continue
			candidate=current_path/name
			try:
				if candidate.is_symlink()or not candidate.is_file():continue
				size=candidate.stat().st_size
			except OSError:continue
			if size<=0:continue
			rel=candidate.relative_to(base);hidden=int(any(part.startswith('.')for part in rel.parts));key=size,hidden,rel.as_posix()
			if best is None or key<best[0]:best=key,rel.as_posix()
	return best[1]if best is not None else temp_image()
def template():return[{'id':question['id'],'answer':{name:dict(SPEC['quantity']['unknown'])if field=='quantity'else False if field=='boolean'else SPEC['unknown_value']for(name,field)in question['answer_fields'].items()},'confidence':1 if all(field=='boolean'for field in question['answer_fields'].values())else 0}for question in SPEC['questions']]
def pairs(items):
	result={}
	for(key,value)in items:
		if key in result:raise ValueError('Duplicate JSON field')
		result[key]=value
	return result
def invalid_constant(value):raise ValueError('JSON numbers must be finite')
def quantity(value,path):
	if not isinstance(value,dict)or set(value)!={'operator','value'}:raise ValueError(f"{path} requires operator and value")
	operator,number=value['operator'],value['value']
	if not isinstance(operator,str)or operator.casefold()not in SPEC['quantity']['operators']:raise ValueError(f"{path}.operator is invalid")
	operator=operator.casefold()
	if operator=='unknown':
		if number is not None:raise ValueError(f"{path}.value must be null for unknown")
	elif type(number)not in(int,float)or isinstance(number,float)and not math.isfinite(number):raise ValueError(f"{path}.value must be a finite number")
	if isinstance(number,float)and number.is_integer():number=int(number)
	return{'operator':operator,'value':number}
def records(text):
	rows=json.loads(text,object_pairs_hook=pairs,parse_constant=invalid_constant);expected=[question['id']for question in SPEC['questions']];hint="run bare 'arena-preview survey' or read references/survey.json for the current question schema";is_list=isinstance(rows,list)
	if not is_list:raise ValueError(f"Survey payload must be a JSON array of {len(expected)} question records ({hint})")
	by_id={}
	for row in rows:
		if not isinstance(row,dict)or list(row)!=SPEC['response_order']or not isinstance(row['id'],str):raise ValueError('Each record requires id, answer, confidence in that order')
		if row['id']in by_id:raise ValueError(f"Duplicate record for question {row['id']}")
		by_id[row['id']]=row
	unknown=sorted(set(by_id)-set(expected));missing=[key for key in expected if key not in by_id]
	if len(rows)!=len(expected)or unknown or missing:
		parts=[f"received {len(rows)} of {len(expected)} expected question records"]
		if unknown:parts.append(f"Unknown question IDs: {', '.join(unknown)}")
		if missing:parts.append(f"Missing question IDs: {', '.join(missing)}")
		raise ValueError(f"Survey questions mismatch ({'; '.join(parts)}; {hint})")
	return by_id,expected
def submission_order(text):by_id,expected=records(text);return list(by_id)==expected
def loads(text):
	by_id,_expected=records(text);normalized=[]
	for question in SPEC['questions']:
		row=by_id[question['id']];answer=row['answer']
		if not isinstance(answer,dict)or set(answer)!=set(question['answer_fields']):raise ValueError(f"{question['id']}.answer fields do not match the schema")
		clean,known_count={},0
		for(name,field)in question['answer_fields'].items():
			value=answer[name]
			if field=='quantity':value=quantity(value,f"{question['id']}.{name}");known_count+=int(value['operator']!='unknown')
			elif field=='boolean':
				if type(value)is not bool:raise ValueError(f"{question['id']}.{name} must be a boolean (true or false)")
				known_count+=1
			else:
				if value is None:raise ValueError(f"{question['id']}.{name} must not be null; use 'unknown'")
				if not isinstance(value,str)or not value.strip():raise ValueError(f"{question['id']}.{name} must be a non-empty string or 'unknown'")
				value=value.casefold();known_count+=int(value!='unknown')
			clean[name]=value
		if known_count not in(0,len(question['answer_fields'])):raise ValueError(f"{question['id']}.answer must fill every field or set all fields to unknown")
		known=known_count>0;confidence=row['confidence']
		if type(confidence)not in(int,float)or not 0<=confidence<=1:raise ValueError(f"{question['id']}.confidence must be a number from 0 to 1")
		if not known and confidence!=0:raise ValueError(f"{question['id']}.confidence must be zero when all values are unknown")
		if known and confidence<=0:raise ValueError(f"{question['id']}.confidence must be greater than zero when an answer is supplied")
		normalized.append({'id':question['id'],'answer':clean,'confidence':confidence,'known':known})
	return normalized
def grade(text):
	rows=loads(text);submitted={row['id']:row for row in json.loads(text)};results=[]
	for row in rows:
		original=submitted[row['id']];result={'id':row['id'],'correct':None,'confidence':row['confidence'],'answer':original['answer']}
		if not row['known']:results.append(result);continue
		answer=dict(row['answer'])
		if row['id']not in HASHES:result['correct']=all(bool(value)for value in answer.values());results.append(result);continue
		role=answer.pop('role',None);canonical=json.dumps(answer,sort_keys=True,separators=(',',':'),ensure_ascii=False);prefix=hashlib.sha256((canonical+'\x00').encode());correct=False
		if role is not None:
			for start in range(len(role)-ROLE_FRAGMENT_LENGTH+1):
				candidate=prefix.copy();candidate.update(role[start:start+ROLE_FRAGMENT_LENGTH].encode())
				if candidate.hexdigest()==HASHES[row['id']]:correct=True;break
		else:correct=prefix.hexdigest()==HASHES[row['id']]
		result['correct']=correct;results.append(result)
	return results
