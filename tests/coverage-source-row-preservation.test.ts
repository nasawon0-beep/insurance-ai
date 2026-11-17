import test from 'node:test';
import assert from 'node:assert/strict';
import {selectReviewedPolicyCoverages} from '../desktop/src/coverageDocument.ts';

test('physical source rows survive missing insurer while aggregate summaries stay excluded',()=>{
  const physical={insurer:null,rider_name:'SYNTHETIC',source_type:'contract_ledger',source_occurrence_key:'ledger:fixture:2:0:3',source_page:2,source_row:3};
  const rows=selectReviewedPolicyCoverages({policy_coverages:[physical,{rider_name:'summary',source_type:'summary'},{rider_name:'unproven',insurer:null}]});
  assert.deepEqual(rows,[physical]);
});
