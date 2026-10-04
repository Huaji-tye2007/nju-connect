import os
import tempfile

# never read or write the real ~/.config/nju-connect while testing; must run
# before nju_connect.paths is imported
_root = tempfile.mkdtemp(prefix="nju-connect-test-")
os.environ["NJU_CONNECT_CONFIG_DIR"] = os.path.join(_root, "config")
os.environ["NJU_CONNECT_STATE_DIR"] = os.path.join(_root, "state")
os.environ["XDG_DATA_HOME"] = os.path.join(_root, "data")
os.environ["XDG_CONFIG_HOME"] = os.path.join(_root, "xdg")
