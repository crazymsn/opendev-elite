import copy
import datetime as dt
import json
import unittest
import test_criteria_dossier as base_fixture
from worker.criteria import POLICY_VERSION, quality_assessment, qualification_status
from worker.demo import selection_evidence


class QualityTests(unittest.TestCase):
    setUp=base_fixture.CriteriaDossierTests.setUp
    tearDown=base_fixture.CriteriaDossierTests.tearDown
    qualification=base_fixture.CriteriaDossierTests.qualification

    def apply_evidence(self,evidence,outcome='include'):
        return self.workflow.apply('qualify',self.qualification(evidence=evidence,outcome=outcome),'reviewer')

    def test_exact_threshold_passes_and_client_total_is_ignored(self):
        evidence=selection_evidence()
        for item in evidence['quality'].values():item['score']=3
        evidence['quality']['problem']['score']=4
        evidence['quality']['total']=-999
        self.apply_evidence(evidence)
        actual=qualification_status(self.store,self.workflow.project(101))
        self.assertTrue(actual['eligible']);self.assertEqual(actual['quality']['total'],80)

    def test_high_total_cannot_offset_a_weak_dimension(self):
        evidence=selection_evidence();evidence['quality']['evaluability']['score']=2
        self.assertGreater(quality_assessment(evidence)['total'],80)
        with self.assertRaisesRegex(ValueError,'every dimension'):self.apply_evidence(evidence)
        for item in evidence['quality'].values():item['score']=3
        with self.assertRaisesRegex(ValueError,'80/100'):self.apply_evidence(evidence)

    def test_scores_require_integers_narratives_and_sources(self):
        for value in (True,3.5,'4',None,-1,5):
            evidence=selection_evidence();evidence['quality']['problem']['score']=value
            with self.subTest(value=value),self.assertRaisesRegex(ValueError,'integer score'):self.apply_evidence(evidence)
        for change in ('sources','narrative','dimension'):
            evidence=selection_evidence()
            if change=='sources':evidence['quality']['problem']['sources']=[]
            elif change=='narrative':evidence['maintenance']=' '
            else:del evidence['quality']['problem']
            with self.subTest(change=change),self.assertRaises(ValueError):self.apply_evidence(evidence)

    def test_two_dated_substantive_maintenance_sources_are_required(self):
        today=dt.datetime.now(dt.timezone.utc).date()
        invalid=[[],[today.isoformat()]*2,[today.isoformat(),(today+dt.timedelta(days=1)).isoformat()],
                 [today.isoformat(),(today-dt.timedelta(days=366)).isoformat()],['2026-02-30',today.isoformat()]]
        for dates in invalid:
            evidence=selection_evidence();evidence['maintenance_dates']=dates
            with self.subTest(dates=dates),self.assertRaisesRegex(ValueError,'two distinct dates'):self.apply_evidence(evidence)
        evidence=selection_evidence();evidence['quality']['maintenance']['sources']=['same-source',' same-source ']
        with self.assertRaisesRegex(ValueError,'two distinct sources'):self.apply_evidence(evidence)

    def test_missing_quality_can_be_held_and_legacy_pass_is_not_grandfathered(self):
        evidence=selection_evidence();del evidence['quality'];evidence['reason_code']='quality_below_threshold'
        self.apply_evidence(evidence,'hold')
        self.assertFalse(qualification_status(self.store,self.workflow.project(101))['eligible'])
        self.store.conn.execute("UPDATE qualifications SET outcome='include',policy_version='sop-2026-09-v2'")
        self.assertEqual(qualification_status(self.store,self.workflow.project(101))['reason'],'qualification_policy_changed')
        self.assertEqual(POLICY_VERSION,'sop-2026-09-v3')

    def test_forged_stored_pass_is_rechecked_by_contact_gate(self):
        evidence=selection_evidence();evidence['quality']['problem']['score']=0
        self.store.conn.execute('UPDATE qualifications SET evidence=?',(json.dumps(evidence),))
        self.assertEqual(qualification_status(self.store,self.workflow.project(101))['reason'],'qualification_evidence_incomplete')
        with self.assertRaises(ValueError):self.workflow.contact_gate(101)


if __name__=='__main__':unittest.main()
