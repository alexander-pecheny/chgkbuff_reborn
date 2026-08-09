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

**Tournament**:
One competition on the rating site, with a start and end date and a type. Entries typed
`Общий зачёт` are aggregate standings rather than a played event and are never counted.

**Tournament type**:
The site's own classification — `обычный`, `Синхрон`, `Асинхрон`. Distinct from the played/remote
split below, which Buff derives itself.

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
