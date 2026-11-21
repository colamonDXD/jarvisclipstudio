const fs = require('node:fs');
const path = require('node:path');
const read = name => JSON.parse(fs.readFileSync(path.join(__dirname,name)));
const {task,request} = read('submitted.json');
const q = read('render.json').render_facts.create_finish;
const coverage = q.caption_coverage;
const masked = q.burned_words.filter(w=>w.text.includes('***')).length;
const f = n => Number(n).toFixed(2);
const rows = [
  ['Manual crops','Applied',request.layout_template+' · '+request.boxes.arrange+' arrangement. Both rectangles were drawn in Create.'],
  ['Caption source',q.transcript_provider,Object.entries(q.decoder_counts).map(([k,v])=>k+' read '+v+' words').join(' · ')],
  ['Streamer-word filter','Ran',q.streamer_words_retained+' words retained from '+q.expected_words+' selected audio words.'],
  ['Burned captions',q.burned_words.length+' words',q.caption_style.size+' px · '+q.caption_style.animation+' · '+q.caption_chunks.length+' caption groups.'],
  ['Speech coverage',coverage.ok?'Passed':'Flagged',coverage.covered_fraction == null ? coverage.uncovered_seconds+' seconds flagged by the audio-level detector.' :
    (100*coverage.covered_fraction).toFixed(1)+'% of '+f(coverage.speech_seconds)+' seconds of detected speech covered. '+coverage.missing_utterances.length+' complete utterances missed.'],
  ['Titles',request.hook_title_enabled?'On':'Off','Saved Create setting for this export.'],
  ['Game-view cuts',(q.applied_game_shots||[]).length+' intervals',(q.applied_game_shots||[]).map(s=>f(s.start)+'–'+f(s.end)+' s · '+s.reason).join('; ')],
  ['Reaction zoom',q.reaction_zoom_seconds?.length?'Applied':'No recorded zoom',(q.reaction_zoom_seconds||[]).map(t=>f(t)+' s').join(', ')],
  ['Matched sound effect',q.reaction_sfx || 'No recorded cue',q.reaction_sfx?'Mixed into the exported audio at the reaction.':'The render recorded no added reaction sound.'],
  ['Swear mask',masked+' replacements',masked?'Matching caption words were replaced with ***.':'No matching swear occurred in the selected caption words.'],
];
const esc = s => String(s).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;');
const html = `<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Kai Cenat · Create render verification</title>
<style>body{margin:32px auto;padding:0 24px;max-width:1120px;background:#fafafa;color:#171717;font:16px/1.55 system-ui}h1{font-size:30px;line-height:1.2}h2{font-size:20px}main{display:grid;grid-template-columns:minmax(230px,320px) 1fr;gap:32px}video{width:100%;border-radius:8px;background:#161616}table{width:100%;border-collapse:collapse;text-align:left}th,td{border-bottom:1px solid #ddd;padding:8px;vertical-align:top}th{font-weight:600}a{color:#214ba0}small{color:#555}@media(max-width:800px){main{display:block}video{max-width:350px}}</style>
<h1>Kai Cenat gaming upload</h1><p>SnipNova Create · Manual Cutter + AI Editing · Auto template</p>
<main><section><video controls playsinline preload="metadata" src="kai-1.mp4"></video><p><a href="kai-1.mp4">Open MP4</a> · <a href="drawn-boxes.png">View drawn boxes</a></p></section><section><h2>Features recorded for this export</h2><table><thead><tr><th>Feature</th><th>Result</th><th>Evidence</th></tr></thead><tbody>${rows.map(r=>'<tr>'+r.map(x=>'<td>'+esc(x)+'</td>').join('')+'</tr>').join('')}</tbody></table>
<p><small>Job ${esc(task)} · <a href="submitted.json">Submitted settings</a> · <a href="render.json">Worker render record</a></small></p></section></main></html>`;
fs.writeFileSync(path.join(__dirname,'review.html'),html);
