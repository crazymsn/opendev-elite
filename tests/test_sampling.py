import unittest
from worker.sampling import select_samples


def blob(path,size=100):return {'path':path,'size':size,'type':'blob','sha':'a'*40}


class SamplingTests(unittest.TestCase):
    def test_julia_c_cpp_and_frontend_sources_are_not_lost(self):
        for path in ('src/server.jl','src/server.c','src/server.cpp','src/App.tsx','src/App.vue','src/main.cs','src/Main.java'):
            with self.subTest(path=path):
                result=select_samples([blob(path),blob('examples/demo.py')])
                self.assertEqual(result['files'][0]['path'],path)
                self.assertEqual(result['files'][0]['role'],'implementation')

    def test_runtime_test_dependency_and_ci_have_distinct_evidence(self):
        tree=[blob(p) for p in ('benchmarks/artifacts.py','server/main.py','server/execution/dispatch.py',
            'server/registry/load.py','server/other.py','server/tests/helpers.py','server/tests/test_limits.py',
            'server/requirements.txt','.github/workflows/tests.yml','.github/workflows/pages.yml')]+[blob('server/conftest.py',0)]
        r=select_samples(tree);by_role={role:[i['path'] for i in r['files'] if i['role']==role] for role in ('implementation','test','dependency_manifest','ci_configuration')}
        self.assertEqual(len(r['files']),6)
        self.assertEqual(by_role['implementation'],['server/main.py','server/execution/dispatch.py','server/registry/load.py'])
        self.assertEqual(by_role['test'],['server/tests/test_limits.py'])
        self.assertEqual(by_role['dependency_manifest'],['server/requirements.txt'])
        self.assertEqual(by_role['ci_configuration'],['.github/workflows/tests.yml'])
        self.assertEqual(r['omitted_counts']['empty'],1)

    def test_segment_filters_do_not_drop_latest_or_vendor_named_packages(self):
        tree=[blob(p) for p in ('src/latest.py','vendor_tools/server.py','contest/main.cpp',
            'vendor/third.cpp','node_modules/tool/index.js','src/generated/proto.py','src/msg_pb2.py','src/index.d.ts')]
        r=select_samples(tree)
        self.assertEqual({p['path'] for p in r['files']},{'src/latest.py','vendor_tools/server.py','contest/main.cpp'})
        self.assertEqual(r['omitted_counts']['generated_or_vendor'],5)

    def test_deterministic_bounded_selection_and_invalid_tree_inputs(self):
        tree=[blob('src/module'+str(i)+'/main.py') for i in range(12)]
        tree += [blob('../outside.py'),blob('/absolute.py'),blob('back\\slash.py'),blob('src/empty.py',0),blob('src/huge.py',80001),blob('src/boolean.py',True)]
        self.assertEqual(select_samples(tree),select_samples(list(reversed(tree))))
        r=select_samples(tree)
        self.assertEqual(len(r['files']),3);self.assertEqual(r['candidate_counts']['implementation'],12)
        self.assertEqual(r['omitted_counts']['invalid'],3);self.assertEqual(r['omitted_counts']['oversized'],2)
        self.assertTrue(all(p['blob_sha']=='a'*40 for p in r['files']))

    def test_root_manifests_precede_example_dependency_files(self):
        r=select_samples([blob('Project.toml'),blob('examples/demo/Project.toml'),blob('tests/requirements.txt')])
        self.assertEqual(r['files'][0]['path'],'Project.toml')
        self.assertEqual(r['candidate_counts']['implementation'],0)


if __name__=='__main__':unittest.main()
