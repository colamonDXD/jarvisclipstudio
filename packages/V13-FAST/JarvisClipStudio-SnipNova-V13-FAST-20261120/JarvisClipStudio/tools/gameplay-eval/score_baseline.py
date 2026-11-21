"""Run the CURRENT no-transcript selector on a gameplay fixture and score it
against the Lane A ground truth. Usage: python score_baseline.py <video> [clip_seconds] [count]"""
import sys, json
sys.path.insert(0, "backend")
from pathlib import Path
from src.auto_director.beat_grid import analyse_audio_beats
from src.auto_director.visual_moments import select_visual_moments
from truth import GROUND_TRUTH, score_moments

video = Path(sys.argv[1]); clip = float(sys.argv[2]) if len(sys.argv) > 2 else 30
count = int(sys.argv[3]) if len(sys.argv) > 3 else 5
grid = analyse_audio_beats(video)
moments = select_visual_moments(video, 150.0, target_count=count, clip_seconds=clip, beat_grid=grid)
for m in moments: print(m["start_time"], m["end_time"], m["reasoning"][:110])
print(json.dumps(score_moments(moments), indent=1))
