# Gate-comparison clip list (tools/phase2/gate_compare.py)

Each case is a 40 s excerpt starting 60 % into the local file, re-encoded libx264 CRF 20.
`Expected` = should the facecam (reaction) layout engage. Twitch files are 300 s sections
downloaded with yt-dlp `--download-sections`; YouTube sources are CC BY (Phase 2 niches).

| Local file | What it is | Expected | Source and exact excerpt |
|---|---|---|---|
| `real_hd/kai_sa_20000_hd.mp4` | Kai Cenat 1080p, cam mid-left | True | https://www.twitch.tv/videos/2922436506 at 1080p60, section 20000-20300 s; excerpt = VOD 20180.0-20220.0 s |
| `real_more/kai_sa_20000.mp4` | Kai Cenat 360p, cam mid-left | True | https://www.twitch.tv/videos/2922436506 at 360p30, section 20000-20300 s; excerpt = VOD 20180.0-20220.0 s |
| `real_more/kai_gta4_30000.mp4` | Kai Cenat GTA IV, small top-left cam | True | https://www.twitch.tv/videos/2924282282 at 360p30, section 30000-30300 s; excerpt = VOD 30180.0-30220.0 s |
| `real_more/kai_gta4_9000.mp4` | Kai Cenat GTA IV, looking down at desk | True | https://www.twitch.tv/videos/2924282282 at 360p30, section 9000-9300 s; excerpt = VOD 9180.0-9220.0 s |
| `real_twitch/vicens_1800.mp4` | Vicens GTA V, cam left | True | https://www.twitch.tv/vicens/video/2930536698 at 360p30, section 1800-2100 s; excerpt = VOD 1980.0-2020.0 s |
| `real_twitch/vicens_4200.mp4` | Vicens GTA V, cam left | True | https://www.twitch.tv/vicens/video/2930536698 at 360p30, section 4200-4500 s; excerpt = VOD 4380.0-4420.0 s |
| `real_twitch/vicens_7200.mp4` | Vicens GTA V, cam left | True | https://www.twitch.tv/vicens/video/2930536698 at 360p30, section 7200-7500 s; excerpt = VOD 7380.0-7420.0 s |
| `real_twitch/vicens_sample.mp4` | Vicens GTA V, cam left | True | Twitch VOD https://www.twitch.tv/vicens/video/2930536698, 240 s sample, section offset NOT recorded (use the three offset clips) |
| `real_more/speed_tw_2400.mp4` | iShowSpeed Omegle split (two people) | False | https://www.twitch.tv/videos/2930619788 at 360p30, section 2400-2700 s; excerpt = VOD 2580.0-2620.0 s |
| `real_more/speed_tw_7000.mp4` | iShowSpeed full-frame cam | False | https://www.twitch.tv/videos/2930619788 at 360p30, section 7000-7300 s; excerpt = VOD 7180.0-7220.0 s |
| `phase2_src/1oXjtK5Nsjw.mp4` | Video-call podcast, two windows | False | https://www.youtube.com/watch?v=1oXjtK5Nsjw (as downloaded (yt-dlp best <=480p)); excerpt = 432.0-472.0 s of the file (file = first 720 s) |
| `phase2_src/D3cAyFmhi_M.mp4` | Travel vlog | False | https://www.youtube.com/watch?v=D3cAyFmhi_M (as downloaded (yt-dlp best <=480p)); excerpt = 603.2-643.2 s of the file |
| `phase2_src/dwZK6iQlyEw.mp4` | Stand-up comedy | False | https://www.youtube.com/watch?v=dwZK6iQlyEw (as downloaded (yt-dlp best <=480p)); excerpt = 367.6-407.6 s of the file |
| `phase2_src/TkHlhgTutFs.mp4` | Talking head speech | False | https://www.youtube.com/watch?v=TkHlhgTutFs (as downloaded (yt-dlp best <=480p)); excerpt = 165.6-205.6 s of the file |
| `phase2_src/-m-gudHhLxc.mp4` | Lecture: slides + presenter window | None | https://www.youtube.com/watch?v=-m-gudHhLxc (as downloaded (yt-dlp best <=480p)); excerpt = 434.9-474.9 s of the file |
