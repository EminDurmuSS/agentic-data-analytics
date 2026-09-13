import copy
import json
from agentic_analytics.agent.context import _compact_source_navigation, model_messages
from agentic_analytics.agent.run_store import canonical


def history():
    messages=[{'role':'user','content':'Inspect the report and explain whether the requested table is available.'}]
    names,args={},{}
    def append(name,key,payload,arguments):
        names[key]=name;args[key]=arguments
        messages.append({'role':'assistant','content':None,'tool_calls':[{'id':key,'type':'function',
            'function':{'name':name,'arguments':canonical(arguments)}}]})
        messages.append({'role':'tool','tool_call_id':key,'content':canonical(payload)})
    for i in range(4):
        append('find_source_pages',f'find{i}',{'status':'ok','source_id':'source-a','raw_sha256':'hash-a','complete':True,
            'searched_pages':list(range(1,81)),'query':f'query{i}','warnings':[{'code':'SCAN_NOTE'}],
            'matches':[{'page':20+i,'excerpt':('search navigation only '*100),'match_type':'partial_terms','all_query_terms':False}]},
            {'source_id':'source-a','query':f'query{i}'})
        append('inspect_source',f'read{i}',{'status':'ok','source_id':'source-a','raw_sha256':'hash-a',
            'processed_pages':[20+i],'pages':[{'page':20+i,'text':(f'Actual body {i} '*240)}],
            'warnings':[{'code':'PARTIAL_PDF_INSPECTION'}], 'tables':[{'table_id':f'table-{i}','page':20+i,'row_count':5,
                'columns':['Label','Amount'],'units':{'Amount':'million TRY'},'layout_review_required':True,
                'quality_notes':['Merged rows require reading'],'preview':[{'Label':'old preview '*100,'Amount':'900 '*100}]}]},
            {'source_id':'source-a','page_numbers':[20+i]})
    append('read_source_table','real-rows',{'status':'ok','source_id':'source-a','page':23,'rows':[
        {'candidate_row':1,'values':{'Label':'Actual amount','Amount':'123.45'}}]}, {'source_id':'source-a','table_id':'table-3'})
    append('inspect_source','other-source',{'status':'ok','source_id':'source-b','raw_sha256':'hash-b',
        'pages':[{'page':1,'text':'OTHER SOURCE BODY'}]}, {'source_id':'source-b','page_numbers':[1]})
    return messages,names,args


def test_pressure_keeps_recent_bodies_numeric_rows_pairing_and_source_identity():
    messages,names,args=history(); original=copy.deepcopy(messages)
    _compact_source_navigation(messages,0,names,args,17000)
    mapped={m['tool_call_id']:json.loads(m['content']) for m in messages if m.get('role')=='tool'}
    old={m['tool_call_id']:json.loads(m['content']) for m in original if m.get('role')=='tool'}
    assert mapped['real-rows']==old['real-rows'] and mapped['other-source']==old['other-source']
    for key in ('read2','read3'):
        assert mapped[key]['pages']==old[key]['pages']
    assert mapped['find3']==old['find3']
    receipt=mapped['read0']
    assert receipt['source_id']=='source-a' and receipt['raw_sha256']=='hash-a'
    assert receipt['warnings']==old['read0']['warnings']
    assert receipt['tables'][0]['units']=={'Amount':'million TRY'}
    assert receipt['tables'][0]['layout_review_required']
    assert receipt['re_read']['arguments']==args['read0']
    assert [(m.get('role'),m.get('tool_call_id'),m.get('tool_calls')) for m in messages]==[(m.get('role'),m.get('tool_call_id'),m.get('tool_calls')) for m in original]


def test_no_pressure_and_prior_turn_are_unchanged():
    messages,names,args=history(); original=copy.deepcopy(messages)
    _compact_source_navigation(messages,0,names,args,1000000)
    assert messages==original
    _compact_source_navigation(messages,len(messages),names,args,1)
    assert messages==original


def test_failed_recovery_response_is_never_archived():
    messages,names,args=history()
    for m in messages:
        if m.get('tool_call_id')=='find0':
            m['content']=canonical({'status':'blocked','source_id':'source-a','errors':[{'code':'SOURCE_READ_REQUIRED'}],
                'recovery':{'suggested_inspection':{'source_id':'source-a','page_numbers':[20]}}})
            expected=m['content']
    _compact_source_navigation(messages,0,names,args,10000)
    assert next(m['content'] for m in messages if m.get('tool_call_id')=='find0')==expected


def test_duplicate_inspections_do_not_displace_the_previous_distinct_read():
    messages,names,args=history()
    original=next(json.loads(m['content']) for m in messages if m.get('tool_call_id')=='read2')
    repeated=next(json.loads(m['content']) for m in messages if m.get('tool_call_id')=='read3')
    for n in range(2):
        key=f'duplicate{n}';names[key]='inspect_source';args[key]=args['read3']
        messages.append({'role':'assistant','content':None,'tool_calls':[{'id':key,'type':'function',
            'function':{'name':'inspect_source','arguments':canonical(args[key])}}]})
        messages.append({'role':'tool','tool_call_id':key,'content':canonical(repeated)})
    _compact_source_navigation(messages,0,names,args,17000)
    protected=json.loads(next(m['content'] for m in messages if m.get('tool_call_id')=='read2'))
    assert protected['pages']==original['pages']
    assert not protected.get('source_content_omitted')
