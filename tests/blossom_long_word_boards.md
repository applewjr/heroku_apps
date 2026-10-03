# Blossom: long-word test boards

Boards that put the longest words the solver can show at the top of /blossom, for checking the results table on narrow phones. Measured 2026-10-03. If the word lists change, recompute using the steps at the bottom.

## Boards

| Word | Length | Center | Petals | Tap petal | Notes |
|---|---|---|---|---|---|
| disinterestednesses | 19 | T | D E I N R S | S | The longest served word. It comes first, scoring 80. "disinterestedness" (17) is on the same board. |
| coccidioidomycosis | 18 | any of its letters | the other six of C D I M O S Y | any | |

About ten 17-letter words follow, for example:

| Word | Letters |
|---|---|
| inattentivenesses | A E I N S T V |
| inexpensivenesses | E I N P S V X |
| interestingnesses | E G I N R S T |
| remorselessnesses | E L M N O R S |

## Measured fit

Measured in headless Chrome, with the scrollbar hidden the way phones overlay it:

| Mode | 360px | 390px |
|---|---|---|
| Normal | The 19-letter word fits on one line, with the table exactly at the screen edge. | Fits, with about 30px to spare. |
| Helper | The table runs **18px past the screen edge**: the masked word ("d" plus 18 underscores) is wider than the word itself. | Fits exactly. |

The 15-letter words that normally top the list fit at 360px in both modes. If helper mode ever needs to cover the 19-letter case too, tighten `td.word-masked` in `templates/blossom.html` a bit more below about 380px.

## Recomputing

The longest words in the base list (4+ letters, at most 7 unique), from the repo root:

```
./env/Scripts/python.exe -c "from data import words_blossom; print(sorted(words_blossom, key=lambda w: (-len(w), w))[:10])"
```

The live list also includes `blossom_added_words` and excludes `blossom_invalid_words`, so check whether anything long has been added there.

To measure layout: Chrome on Windows won't make a window narrower than about 500px, so load the page inside an `<iframe style="width:360px">` and screenshot that. Media queries follow the iframe's width.
