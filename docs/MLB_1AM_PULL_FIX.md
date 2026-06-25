# MLB 1 AM ET pull fix

MLB must begin HOT capture at 1:00 AM ET and continue every 15 minutes.

Required deployed schedule:

- `MLBHotKickoff1amET`: daily kickoff at 1:00 AM ET during MLB season.
- `MLBHotEvery15Min`: continuous HOT capture every 15 minutes.

Build proof must show:

- first pull time
- latest pull time
- actual pull count
- expected pull count since 1:00 AM ET
- whether overnight coverage is complete
