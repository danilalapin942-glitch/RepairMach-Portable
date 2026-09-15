
import unittest
import os, sys

curr_path = os.path.dirname(os.path.realpath(__file__))
vsp_path = os.path.join(curr_path, '../..')
sys.path.insert(1, vsp_path)

from openvsp import *

class TestOpenVSP(unittest.TestCase):
	def setUp(self):
		VSPRenew()
		# Start from a clean error queue so each example is only judged on the
		# errors it raised itself.
		api_err_mgr = ErrorMgrSingleton.getInstance()
		while api_err_mgr.GetNumTotalErrors() > 0:
			api_err_mgr.PopLastError()
	def tearDown(self):
		# An example that leaves an API error behind has not worked, whether or
		# not it bothered to check anything itself.  An example that raises one
		# on purpose is expected to take it back off the queue.
		api_err_mgr = ErrorMgrSingleton.getInstance()
		api_err_msgs = []
		while api_err_mgr.GetNumTotalErrors() > 0:
			api_err_msgs.append( api_err_mgr.PopLastError().GetErrorString() )
		assert len( api_err_msgs ) == 0, "API errors: " + "; ".join( api_err_msgs )
	def test_IsFacade(self):
		is_facade = IsFacade()
	def test_IsGUIRunning(self):
		is_gui_active = IsGUIRunning()

if __name__ == '__main__':
    unittest.main()
