# Why citation page numbers look different

"1-7" is the page number printed in the manual. "49" is the actual PDF page
number, which we assign while pre-analyzing the document — we use that one to jump
to the exact page and to place the highlight, because you can't navigate using the
printed "1-7". Both are correct; they're just two different page numbers for the
same page.

We show the printed one ("1-7") by default because it matches what's in the
manual. We only fall back to showing the physical one ("49") when a page has no
number printed on it (covers, figures, front matter).
