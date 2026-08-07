import subprocess, sys
done = subprocess.run(sys.argv[1:], timeout=60, stderr=subprocess.STDOUT)
sys.exit(done.returncode)
