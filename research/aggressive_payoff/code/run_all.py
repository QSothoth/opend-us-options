#!/usr/bin/env python3
"""Complete search/evidence, then frozen perturbation checks without refitting."""
import subprocess,sys
from pathlib import Path
if '--out' not in sys.argv or '--zip' not in sys.argv:raise SystemExit('run_all.py --zip DATA.zip --out RESULTS [--workers 6]')
root=Path(__file__).parent
subprocess.run([sys.executable,str(root/'run.py'),*sys.argv[1:]],check=True)
subprocess.run([sys.executable,str(root/'perturbations.py'),*sys.argv[1:]],check=True)
subprocess.run([sys.executable,str(root/'component_diagnostics.py'),*sys.argv[1:]],check=True)
subprocess.run([sys.executable,str(root/'posthoc_candidate.py'),*sys.argv[1:]],check=True)
out=sys.argv[sys.argv.index('--out')+1];data=sys.argv[sys.argv.index('--zip')+1]
subprocess.run([sys.executable,str(root/'verify_frozen.py'),'--zip',data,'--reference',out,'--out',str(Path(out)/'verification')],check=True)
subprocess.run([sys.executable,str(root/'finalize_report.py'),'--out',out],check=True)
