import unittest
from workcontext.runtime import order
class Runtime(unittest.TestCase):
 def test_dependencies_and_cycles(self):
  self.assertEqual([x['name'] for x in order([{'name':'worker','depends_on':['coordinator']},{'name':'coordinator'}])],['coordinator','worker'])
  for items in [[{'name':'a','depends_on':['a']}],[{'name':'a','depends_on':['missing']}],[{'name':'a'},{'name':'a'}]]:
   with self.assertRaises(ValueError):order(items)
if __name__=='__main__':unittest.main()
