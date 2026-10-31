import datetime as dt
import json
import unittest
import test_criteria_dossier as base_fixture
from worker.criteria import catalog, qualification_status
from worker.demo import selection_evidence, review_one, experience, SHA


class SelectionPolicyTests(unittest.TestCase):
    setUp = base_fixture.CriteriaDossierTests.setUp
    tearDown = base_fixture.CriteriaDossierTests.tearDown
    qualification = base_fixture.CriteriaDossierTests.qualification
    def assessment(self, outcome='include', **changes):
        data=self.qualification(outcome=outcome)
        evidence={**data['evidence'],**changes.pop('evidence',{})}
        return {**data,**changes,'evidence':evidence}

    def apply(self, **changes):
        return self.workflow.apply('qualify',self.assessment(**changes),'reviewer')

    def test_timestamp_or_bot_activity_cannot_confirm_active_maintenance(self):
        for kind in ('timestamp_only','unknown'):
            with self.subTest(kind=kind), self.assertRaisesRegex(ValueError,'not timestamps'):
                self.apply(evidence={'maintenance_kind':kind})

    def test_missing_or_future_maintenance_date_is_rejected(self):
        for date in ('', '2026-02-30', (dt.datetime.now(dt.timezone.utc).date()+dt.timedelta(days=1)).isoformat()):
            with self.subTest(date=date), self.assertRaisesRegex(ValueError,'observation date'):
                self.apply(evidence={'maintenance_date':date})

    def test_stable_project_is_not_rejected_by_an_activity_day_cutoff(self):
        dates=[(dt.datetime.now(dt.timezone.utc).date()-dt.timedelta(days=d)).isoformat() for d in (270,240)]
        self.apply(evidence={'maintenance_kind':'response','maintenance_date':dates[-1],'maintenance_dates':dates})
        self.assertTrue(qualification_status(self.store,self.workflow.project(101))['eligible'])

    def test_use_case_delivery_and_access_evidence_are_required_for_inclusion(self):
        for field in ('use_case','delivery','access_risk'):
            with self.subTest(field=field), self.assertRaisesRegex(ValueError,'use-case, delivery'):
                self.apply(evidence={field:''})

    def test_unknown_evidence_can_be_held_without_inventing_a_product_trial(self):
        self.apply(outcome='hold',maintenance='uncertain',evidence={'reason_code':'not_ready','maintenance_kind':'unknown',
            'maintenance_date':'','use_case':'','delivery':'','access_risk':''})
        self.assertFalse(qualification_status(self.store,self.workflow.project(101))['eligible'])
        self.assertEqual(self.store.one('SELECT COUNT(*) AS n FROM experiences')['n'],0)

    def test_outcome_and_reason_cannot_contradict_each_other(self):
        for outcome,code in [('include','rights_uncertain'),('hold','proven_misconduct'),('exclude','none'),('exclude','invented')]:
            with self.subTest(outcome=outcome,code=code), self.assertRaisesRegex(ValueError,'reason must match'):
                self.apply(outcome=outcome,evidence={'reason_code':code})

    def test_exclusion_facts_must_match_abandonment_and_scope(self):
        for code in ('abandoned','outside_scope'):
            with self.subTest(code=code), self.assertRaises(ValueError):
                self.apply(outcome='exclude',evidence={'reason_code':code})
        self.apply(outcome='exclude',maintenance='inactive',evidence={'reason_code':'abandoned'})
        self.apply(outcome='exclude',scope_fit='no',category='out-of-scope',evidence={'reason_code':'outside_scope'})

    def test_untried_or_environment_failed_product_is_not_rejected_as_no_value(self):
        with self.assertRaisesRegex(ValueError,'second-round rejection'):
            self.apply(outcome='exclude',evidence={'reason_code':'no_real_value'})
        review_one(self.workflow)
        eid=experience(self.workflow,'human',success=False)
        result=json.loads(self.store.one('SELECT result FROM experiences WHERE id=?',(eid,))['result'])
        result['failure_kind']='environment'
        self.store.conn.execute('UPDATE experiences SET result=? WHERE id=?',(json.dumps(result),eid))
        self.workflow.apply('review',{'project_id':101,'round':2,'sha':SHA,'decision':'reject','rationale':'Synthetic evaluation',
            'evidence':{'experience_ids':[eid]}},'reviewer')
        with self.assertRaisesRegex(ValueError,'environment failure'):
            self.apply(outcome='exclude',evidence={'reason_code':'no_real_value'})
        result['failure_kind']='product'
        self.store.conn.execute('UPDATE experiences SET result=? WHERE id=?',(json.dumps(result),eid))
        self.apply(outcome='exclude',evidence={'reason_code':'no_real_value'})

    def test_stored_inclusion_missing_required_evidence_cannot_bypass_gate(self):
        evidence=selection_evidence();del evidence['delivery']
        self.store.conn.execute('UPDATE qualifications SET evidence=?',(json.dumps(evidence),))
        self.assertEqual(qualification_status(self.store,self.workflow.project(101))['reason'],'qualification_evidence_incomplete')

    def test_end_user_and_agent_applications_fit_without_stars_or_funding(self):
        for category in ('applications','agent-applications'):
            self.apply(category=category)
            self.assertTrue(qualification_status(self.store,self.workflow.project(101))['eligible'])

