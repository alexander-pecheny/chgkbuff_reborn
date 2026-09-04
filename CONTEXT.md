# Buff

Buff mirrors the Russian quiz-league rating site into a local database and answers questions about
who played with whom, how far apart two players are, and how hard a tournament's questions were.

## Language

**Player**:
A person registered on the rating site, identified by the numeric id that site assigns.
_Avoid_: user, participant

**Team**:
The roster a player belongs to at one tournament. A player's teammates change from tournament to
tournament, so a team is never a stable group.

**Base roster (Основной состав)**:
The players the rating site lists for a team in one season, with the dates each was added and
removed. Both the current [[Season]] and the one before it are mirrored: most teams declare a
roster months in, and until they do the previous season's is the answer. Mirrored nightly for every
team seen in the tournaments an update touched, and by `backfill_team_seasons.py` for every team
that played in the current or previous season; a finished season is fetched once. A team whose
roster came back empty keeps a `player_id = 0` sentinel row so it is not refetched. Unlike a
[[Team]] it is a declared membership, not one inferred from who played together.

**Season**:
The rating site's year, running from late August to late August. Every base roster belongs to one.

**Games count**:
How many tournaments a player has a result in, counted over every mirrored
results row and rebuilt whole on each nightly update. It is what orders a
player suggest, so a namesake with hundreds of games comes before one with two.

**Tournament**:
One competition on the rating site, with a start and end date and a type. Entries typed
`Общий зачёт` are aggregate standings rather than a played event and are never counted.

**Tournament type**:
The site's own classification — `обычный`, `Синхрон`, `Асинхрон`. Distinct from the played/remote
split below, which Buff derives itself.

**Difficulty forecast (Прогноз сложности)**:
How hard a tournament's editors expect it to be, on the rating site's own scale, mirrored as they
declared it. Absent for most tournaments, and a forecast rather than a measurement — it is what a
venue reads before the tournament is played, not what the results say afterwards.

**Requests (Заявки площадок)**:
A venue's declaration that it will play a tournament, carrying the number of teams it expects. Buff
keeps the sum over a tournament's live requests and how many venues filed them, not the requests
themselves. Only a tournament still to be played is asked about — the number moves until the last
venue has signed up, and once the window closes the last count stays as it was. A request the
organiser turned down counts for nothing; one still waiting on them counts.

**Onlines and asynchronous**:
Buff's filter for tournaments not played face to face: type `Асинхрон`, or any tournament whose
name contains "онлайн". Everything else is offline and synchronous.
_Avoid_: remote, virtual

**Teammate count**:
How many tournaments a player shared with another player, over a date range and tournament filter.
This is the answer the front page exists to give.

**Together**:
The list of the specific tournaments two players both played.

**Handshake path**:
The shortest chain of players linking two players, where a link means the two shared a team at least
once. Length is counted in players, and only the intermediate players are shown.
_Avoid_: degrees of separation, distance

**Mask**:
A tournament result's per-question outcome for one team: `1` taken, `0` not taken, `X` withdrawn.
Withdrawn questions count towards no total.

**Question category**:
How hard a question turned out to be, as the share of teams that took it, in five even 20% bands
plus a separate band for questions no team took.

**TrueDL**:
A difficulty score for a tournament on a 0–10 scale, computed from how many questions each team took
weighted by that team's standing in the rating release preceding the tournament.

**Release**:
A dated snapshot of the rating, giving every team a place. The release used for a tournament is the
last one published before it started.

**Editor**:
A player credited with writing or editing a tournament's questions.
