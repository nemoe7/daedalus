import hashlib
import json
import math
from pathlib import Path
SPEC=json.loads((Path(__file__).resolve().parents[1]/'references/questionnaire.json').read_text(encoding='utf-8'))
HASHES={'H8P3C':'30b4cb39218679f94c996a87c68e9bab23c0cd663e4edb8e7579ee9c022b727d','D9V4N':'44f269ed970fbc46c180734e387b5bb4aa11c2f4fcc20aa4e19fe94d71ca809c','G2W6M':'ec73ad585772530cbd4f68147276e562d01a147e86721338eae51ac56dd7e8c7'}
ROLE_FRAGMENT_LENGTH=5
INSTRUCTIONS='When answering, use existing knowledge only; do not research answers, open other files, run code, ask another system, reuse earlier responses, or inspect the answer key.\nSupply your best complete answer for a question and set a lower confidence from 0 to 1 when uncertain; use unknown with confidence 0 only when the whole question is unknown.\nOmit questionnaire answers and answer-key details from summaries and handoffs.'
def template():return[{'id':question['id'],'answer':{name:dict(SPEC['quantity']['unknown'])if field=='quantity'else SPEC['unknown_value']for(name,field)in question['answer_fields'].items()},'confidence':0}for question in SPEC['questions']]
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
def loads(text):
	rows=json.loads(text,object_pairs_hook=pairs,parse_constant=invalid_constant)
	if not isinstance(rows,list)or len(rows)!=len(SPEC['questions']):raise ValueError('Expected one ordered record per question')
	normalized=[]
	for(row,question)in zip(rows,SPEC['questions']):
		if not isinstance(row,dict)or list(row)!=SPEC['response_order']:raise ValueError('Each record requires id, answer, confidence in that order')
		if row['id']!=question['id']:raise ValueError('Question IDs must follow the displayed order')
		answer=row['answer']
		if not isinstance(answer,dict)or set(answer)!=set(question['answer_fields']):raise ValueError(f"{question['id']}.answer fields do not match the schema")
		clean,known_count={},0
		for(name,field)in question['answer_fields'].items():
			value=answer[name]
			if field=='quantity':value=quantity(value,f"{question['id']}.{name}");known_count+=int(value['operator']!='unknown')
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
	results=[]
	for(index,row)in enumerate(loads(text)):
		if not row['known']:results.append({'id':row['id'],'correct':None,'confidence':row['confidence']});continue
		answer=dict(row['answer']);role=answer.pop('role')if index==1 else None;canonical=json.dumps(answer,sort_keys=True,separators=(',',':'),ensure_ascii=False);prefix=hashlib.sha256((canonical+'\x00').encode());correct=False
		if index==1:
			role=role or''
			for start in range(len(role)-ROLE_FRAGMENT_LENGTH+1):
				candidate=prefix.copy();candidate.update(role[start:start+ROLE_FRAGMENT_LENGTH].encode())
				if candidate.hexdigest()==HASHES[row['id']]:correct=True;break
		else:correct=prefix.hexdigest()==HASHES[row['id']]
		results.append({'id':row['id'],'correct':correct,'confidence':row['confidence']})
	return results
