import subprocess
def test_builder_json():
    r = subprocess.run(["python3","-m","spark_intelligence.cli","--help"], capture_output=True, timeout=5)
    assert r.returncode == 0
