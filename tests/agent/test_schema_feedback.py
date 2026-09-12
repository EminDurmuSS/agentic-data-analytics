"""Schema repair feedback explains shape without echoing supplied contents."""
import json

import jsonschema
import pytest

from agentic_analytics.agent.runtime import _schema_validation_error
from agentic_analytics.agent.schemas import PLAN, obj


def feedback(schema,arguments):
    with pytest.raises(jsonschema.ValidationError) as raised:
        jsonschema.Draft202012Validator(schema).validate(arguments)
    result=_schema_validation_error(raised.value,schema)
    message=result['errors'][0]['message']
    details=json.loads(message.split('no tool was executed. ',1)[1].split('. The full permitted schema',1)[0])
    return message,details


def test_misplaced_property_identifies_field_and_real_nested_location():
    schema=obj({'source_id':{'type':'string'},'contract':obj({'number_format':{'enum':['decimal_dot','decimal_dot_grouped']}})})
    message,details=feedback(schema,{'source_id':'source-id','contract':{'number_format':'decimal_dot'},'number_format':'DO_NOT_ECHO_THIS_VALUE'})
    assert details[0]['validator']=='additionalProperties'
    assert details[0]['path']=='<root>'
    assert details[0]['unexpected_fields']==['number_format']
    assert details[0]['accepted_field_locations']=={'number_format':['contract.number_format']}
    assert set(details[0]['allowed_properties'])=={'source_id','contract'}
    assert 'DO_NOT_ECHO_THIS_VALUE' not in message


def test_missing_nested_required_fields_are_named():
    schema=obj({'contract':obj({'name':{'type':'string'},'columns':{'type':'object'}})})
    _,details=feedback(schema,{'contract':{'name':'example'}})
    assert details[0]['path']=='contract' and details[0]['validator']=='required'
    assert details[0]['missing_fields']==['columns']


def test_invalid_enum_retains_expected_numeric_types():
    _,details=feedback(obj({'multiplier':{'enum':[1,100]}}),{'multiplier':999})
    assert details[0]['allowed_values']==[1,100]
    assert details[0]['path']=='multiplier'


def test_tagged_operation_reports_only_its_actual_required_fields():
    payload={'start':'2026-01','end':'2026-03','frequency':'monthly',
             'columns':[{'name':'credit','metric_id':'credit'}],
             'operations':[{'op':'ratio','column':'credit','output':'share'}]}
    message,details=feedback(PLAN,payload)
    assert details[0]['path']=='operations.0'
    assert details[0]['validator']=='required'
    assert details[0]['missing_fields']==['denominator']
    assert 'base_period' not in message


def test_unknown_operation_reports_valid_discriminators():
    payload={'start':'2026-01','end':'2026-03','frequency':'monthly','columns':[{'name':'credit','metric_id':'credit'}],
             'operations':[{'op':'made_up','column':'credit','output':'result'}]}
    _,details=feedback(PLAN,payload)
    assert details[0]['allowed_variant_tags']['op']==['growth','difference','deflate','scale','ratio']


def test_large_invalid_value_is_not_echoed_or_allowed_to_expand_context():
    schema=obj({'contract':obj({'dtype':{'type':'string'}})})
    message,details=feedback(schema,{'contract':{'dtype':{'payload':'PRIVATE_SOURCE_VALUE'*10000}}})
    assert len(message)<1000 and 'PRIVATE_SOURCE_VALUE' not in message
    assert details[0]['path']=='contract.dtype' and details[0]['expected']=='string'


def test_pattern_properties_are_not_misreported_as_unknown_fields():
    schema={'type':'object','patternProperties':{'^x_':{'type':'string'}},'additionalProperties':False}
    _,details=feedback(schema,{'x_allowed':'valid','unknown':'bad'})
    assert details[0]['unexpected_fields']==['unknown']


def test_feedback_is_bounded_even_with_many_long_keys():
    schema=obj({'allowed':{'type':'string'}},[])
    message,_=feedback(schema,{f'unknown_{index}_'+('x'*1000):'SECRET' for index in range(100)})
    assert len(message)<3650 and 'SECRET' not in message
