export default function ShotReview() {
  const clips = [
    { name: "002 · The breathing prompt", shots: [
      ["0.00–2.65", "Gameplay", "The controller instruction establishes what the avatar is doing. At 2.20 s, a 150 ms dissolve skips ahead to the first response."],
      ["2.65–21.20", "Streamer and game", "Kai reacts as the avatar completes the breathing sequence and stands."],
      ["21.20–26.34", "Gameplay", "The camera follows the change of scene at the dock."],
      ["26.34–30.50", "Streamer and game", "Kai delivers his verdict and the clip ends before the bag dialogue."]
    ]},
    { name: "003 · The warning in the field", shots: [
      ["0.00–2.65", "Gameplay", "The avatar approaches the wreckage."],
      ["2.65–14.85", "Streamer and game", "The pickup and Kai’s response establish what the player found."],
      ["14.85–19.30", "Gameplay", "The warning and confrontation take the whole frame."],
      ["19.30–34.00", "Streamer and game", "The pursuit plays continuously with the original sound."]
    ]}
  ];
  return <main style={{maxWidth:800,margin:"24px auto",padding:"0 20px",fontFamily:"system-ui"}}>
    <h1>Kai · edit decisions</h1><p>Times are seconds from the start of each development export.</p>
    {clips.map(clip=><section key={clip.name}><h2>{clip.name}</h2>
      {clip.shots.map(([time,shot,why])=><p key={time}><strong>{time} s · {shot}</strong><br/>{why}</p>)}
    </section>)}
    <p>Source: the final live EDLs, FFmpeg filter graphs and decoded frames, 20 November 2026.</p>
  </main>
}
