Read page $page_number of a biotech pitch deck and describe exactly what is on it.

An image of the page is attached. Use it to read content that the PDF text layer
cannot express: charts, plots, gels, micrographs, pathway and mechanism
diagrams, timelines, and any text baked into images or present only as a scan.

## Text already extracted from the PDF text layer

$text_layer

## Tables already extracted mechanically

$tables

## What to do

1. **Transcribe what the text layer missed.** Put it in `recovered_text`,
   verbatim and in reading order. If the text layer above is empty or clearly
   incomplete (a scanned page), transcribe the whole page. If the text layer is
   complete, transcribe only text that lives inside figures — axis labels, data
   labels, legends, annotations, footnotes under charts. Never repeat text that
   is already in the text layer above.

2. **Describe each visual element.** For every chart, diagram, image or plot,
   record what kind it is, what it shows, and its axis labels and legend
   entries exactly as printed. Mark `is_data_bearing` true only when it presents
   experimental or clinical results rather than illustration or branding.

3. **Capture the numbers.** Record every quantitative value visible on the page.
   - If a value is printed as text (a data label, a table cell, body text),
     copy it exactly and set `is_read_from_axis` to false.
   - If you must estimate a value by reading a bar height or a point position
     against an axis, give your best reading and set `is_read_from_axis` to
     true. This flag is load-bearing: downstream analysis treats estimated
     values as lower-confidence.
   - Include the series, arm, dose or timepoint the value belongs to in
     `context`, exactly as labelled.

4. **Reproduce tables** that appear only as images, as markdown, preserving the
   printed values.

5. **Judge legibility.** If the scan is poor, the resolution is low, or labels
   are unreadable, say so with a low `legibility` score and do not guess at
   unreadable values.

## Critical constraints

- Describe only what is visibly present. Do not infer what a chart "probably"
  shows, do not complete a truncated axis, and do not supply units that are not
  printed.
- A mechanism diagram is a hypothesis drawn by the company, not data. Describe
  it as a diagram.
- If the page is a title slide, team slide, or other non-scientific content,
  summarise it briefly and set `contains_scientific_content` to false.
