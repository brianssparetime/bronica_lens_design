# A hobo toolkit for designing multi-element lenses from surplus $5 glass

My goal was to create a swirly, ~100mm ~f/2.8 lens for the Bronica S2, using only $5-7 elements bought from [SurplusShed](https://www.surplusshed.com/search_lenses.php), and with only the focal length, diameter, and lens shape (the only information SurplusShed provides).  

This repo focuses on the python tools I built for the optical design.  The lens housing has its own github repo [here](https://github.com/brianssparetime/bronica-lens-housing).

## Disclaimer

This is NOT a good way to make a lens.  I'm using 1930s optical formulas (paraxial thin-lens propagation and third-order Seidel sums), and ignoring pretty much all modern ray-tracing abilities.  There are real, impassable limits to what can be modeled with this approach, leaving more to guesswork and trial and error than I would like.

Given those issues, why would anyone in their right mind do it?  Ray tracing requires reasonably precise measurements of the radius of curvature of each element face, and that in turn requires special equipment.

In retrospect, it would have been easier to build a radius measuring tool of my own and just write a ray tracer.  If I do a version 3 of this project, that will be my direction.

I have also taken little care to clean up the code here.  I realized pretty early on that this code has no future for me beyond the culmination of this project, and so my interest in making it beautiful was minimal.  It's good enough for my purposes here.

## The lenses

I built three lenses:

- **v13f, 101mm f/2.9** — a negative meniscus singlet in front, a cemented achromat in the middle, and a positive plano-convex singlet at the rear, with the stop behind the middle element.  Instead of swirling, this lens tie-dyes aggressively outside its sharp center.  This design is technically a failure (relative to my stated goals), but the best kind of failure, one that unlocked more knowledge and left me something usable as a byproduct.
- **v15a, 90mm f/3.1** — three cemented achromats instead of singlets: a negative one in front, two positive ones behind it, with the stop between the two positives.  It swirls.  Mostly.  The center is sharp, then there's a ring where the background smears into arcs around the center, and then partway out the smear flips back to radial and the corners tie-dye.
- **v16d, 98mm f/3.2** — the same plan as the 15a, but with the stop much deeper behind the middle element and the spacings wider, to push the swirl ring outward.  The ring moved outward, but not all the way.

The json export and the optical plan svg for each design, including the intermediate ones, are in `designs/`.

[For an evaluation of those lenses, check out my blog post on this.]()

## The process

On [my first attempt](https://www.brianssparetime.com/posts/my-first-attempt-at-designing-and-building-a-lens-from-scratch/), I did all the design in Google Sheets using simple lens equations.  I came up with a basic two element design, and tried to buy elements that approximately fit.  It didn't work.  Like at all.

This time, I decided to treat the design as an optimization problem over the lens elements I have, against an evolving specification describing my desired lens characteristics.

First, I wrote clear design requirements (`DESIGN-REQS.md`), grouped by priority and reasonably quantitative.  Physical geometric limitations of the mount, recessed elements, and minimum element spacing were hard requirements.  After that, the rough goals of 100mm focal length and f/2.8 aperture.  Finally the description of swirl.  All else equal, designs that are less sensitive to spacing errors in lens positioning are better.  All else equal, compact lenses are better.

Color correction, I decided early on, was beyond the scope of this project.  Designing for swirl and center sharpness was hard enough.

Then, using Claude, I built the rest of the foundations:

- a database of the lens elements I have, and a database of potential designs (referencing the elements, but adding positions and the stop)
- an evaluator tool that holds my optical equations and estimates the properties of a given design
- a sweep tool that would plug different designs into the evaluator
- a tool to let Claude search Surplus Shed's lens finder, useful for discovering new elements
- documents for key design insights, and for cataloging generations of designs

Over the course of a few months, I iterated on improving the optical design, learning more about what makes swirl, buying more elements to unlock new designs, refining my understanding of the S2 geometry to make my design requirements stricter and more realistic, and improving the optical tooling to keep up with the rest.

I don't want to trivialize the importance of iteration.  Easily 95% of the work has been in months of gradual iteration, on top of a simple but clear initial foundation that took only a day or two.  Every advancement in any area unlocks possibilities in the others, and it takes a while to explore the space.

Each generation included a half dozen or more variations, trading off between adherence to focal length, aperture, swirl, flatness in the center, and overall size.  Seeing concrete options really helped me figure out what I prioritize, and to make the design requirements reflect that better for the next generation.

Spacing sensitivity, or the extent to which errors in the spacing between elements affect the optical properties, turned out to be a key insight.  Calculating the spacing sensitivity as part of the evaluation tool allowed me to include it as a low priority design goal, so my design would be minimally disrupted by any manufacturing deviations in the physical housing.

I added a few additional tools during that process:

- a comparison tool for looking at two designs side by side
- a spacing diagram tool to help with the lens housing
- an export tool for turning lens designs into json for the lens housing
- an optical plan diagram generator

After 16 generations, with the last one covering a search space of more than half a million possible designs, I had found my finalists, and decided to stop this insanity.

## The tools

### `inventory.py`

`inventory.py` manages `inventory.db`, the list of elements I have.  Each element has a stock code, a type (BCX, DCX, PCX, PCV, NMN, PMN, ACH, etc), focal length, diameter, where I got it, and max thickness, edge thickness and mounted outer diameter from the calipers if I've measured them.  If the thickness is null, the evaluator assumes 10mm.

```
python inventory.py list
python inventory.py add L14929 ACH 150 38.6 SHED20250224 --max-thickness 9.0 --edge-thickness 6.3
```

### `ss_lens_finder.py`

This is a wrapper for Surplus Shed's lens finder.  The search form has a few quirks (the from/to range is backwards, and biconcaves are split across the BCV and DCV labels), and the wrapper deals with them so I don't have to remember.  I used this to find elements of a given type, focal length and diameter when a design needed one I didn't have.

```
python ss_lens_finder.py -t NMN --dia-min 45 --dia-max 55 --fl-min -130 --fl-max -70
python ss_lens_finder.py --buy-link L5063
```

### `evaluate.py`

This is the main tool.  You give it a list of element codes and the spacings between them, plus a stop position, and it calculates:

- EFL, back focal distance, back clearance (the distance from the rearmost glass to the film, which is what has to clear the mirror), and f-number
- whether the design passes the Bronica S2 hard requirements: mirror and flange clearance, and throat diameter at each element's depth
- the Petzval sum, expressed as a swirl ratio relative to a Dallmeyer Petzval
- the sign of the astigmatism, which determines whether the corners swirl or tie-dye
- third-order spherical aberration and a coma indicator, and the radius of the sharp center
- the sensitivity of EFL and back clearance to small errors in each spacing
- the best stop position for a target f-number

The `-w` option adds a hypothetical element to the inventory (marked as not purchased), so you can test a design before buying anything.  Every evaluated design is saved to `lens_designs.db`.  `--rebuild-all` re-evaluates every saved design with the current math.  I used this a lot, since the math changed with nearly every generation.

```
python evaluate.py v16d L1198 36 L11561 38 L14929 stop 2 24
python evaluate.py --from-db v16d --no-save --field-shape
python evaluate.py v16d --set-inf-offset -26
```

Spacings are center to center, in mm.  `stop 2 24` puts the stop 24mm behind the second element.

### `sweeps/`

There is one sweep script per generation, from v12 to v15.  Each one enumerates combinations of elements and spacings from the inventory, throws out anything that fails the hard requirements, and ranks the rest by that generation's scoring.  The scoring changed every generation, so there are separate scripts rather than one script with options.  Run them from the top level directory so they can find the databases.

```
python sweeps/sweep_v15.py --top 20 --swirl-only
```

### `compare.py` and `spacing_diagram.py`

`compare.py` prints two to four saved designs side by side, with every metric and every spacing, so you can see what changed.  `spacing_diagram.py` draws a saved design in side view against the film plane, the flange, the mirror clearance line, and the throat, so you can check that it physically fits in the camera before printing anything.

```
python compare.py v15a v16d
python spacing_diagram.py v16d
```

### `export.py` and `optical_diagram_maker.py`

`export.py` writes a saved design to the json file the housing reads: element codes and dimensions, spacings, stop depth and hole diameters, and field of view.  `optical_diagram_maker.py` draws the optical plan to scale as an svg.  The housing engraves it on the side of the barrel.

```
python export.py --from-db v16d designs/v16d.json
python optical_diagram_maker.py v16d -o designs/v16d.svg
```

### The databases

`inventory.db` holds the elements, plus any hypothetical elements added with `-w`.  `lens_designs.db` holds every design I've evaluated, with its layout and metrics.  One column, `inf_offset`, is entered by hand with `--set-inf-offset`.  It is the measured shift of the whole optical core needed to reach infinity focus on the actual camera (negative means toward the film).  Once it is set, every position the tools report includes it, so the export and the diagrams match the lens as built.

The evaluator and the sweeps write to the databases.  The other tools only read them.

## AI

AI is very good at building tools, and running optimizations when given good tools and clear metrics for success.

AI is decent at best, and most often poor, at optical engineering past the simple level.  But it's much better at doing research and writing python that encapsulates equations.

My hope was to leverage the strengths of AI by building the tools I needed to let the design fall out of them, and in this I think I was largely successful.

I don't want to give the impression that this was either painless, or magical and straightforward.  It was none of those.

More times than I can count, Claude confidently told me things that were straight up false, or got lazy and didn't actually do the math.  Most of the key insights came not from Claude, but from me poking holes in Claude's reasoning, pointing out contradictions, and constantly asking for confirmation against web research.

Vigorous and rigorous cross-examination is the best, and really only, way to be productive.  If you can bully your AI into flip flopping more than 2-3 times, you need to think harder, back up, and find some way to better ground the discussion in reality.

For example, I pursued the idea of an image simulator for previewing swirl effects for some time, before realizing it was just an elaborate Potemkin village that Claude built for me.  Realistic simulation is just not pragmatic without surface radius measurements and ray tracing.  But walking down this path was a good lesson in exactly how far I could push this design process.

I am sure that at least some of the tools have bits that are bullshit.  For example, through many early generations, my swirl metric was somewhat bullshit: it roughly mapped to what I want, but ignored some potential contributing factors, while including tie-dye effects (where the bokeh smudge radially outwards rather than tangentially around the center).

But I've been pushing and testing on this for months, and the proof is in the pudding: my lenses work well enough, given the severe constraints imposed on their design.

## Blog posts

The whole project is written up in four parts:

- [Part 1: the lenses](https://brianssparetime.com/posts/zenzanot-1-lens/)
- [Part 2: the 3d printed lens housing and SCADwright](https://brianssparetime.com/posts/zenzanot-2-housing/)
- [Part 3: the lens design process](https://brianssparetime.com/posts/zenzanot-3-process/)
- [Part 4: producing swirl in optical designs](https://brianssparetime.com/posts/zenzanot-4-swirl/)

## Lens design resources

- [LensRentals lens genealogy](https://www.lensrentals.com/blog/2011/08/lens-geneology-part-1), great for learning about basic designs without a ton of math (singlet, doublet, triplet, tessar, tele/retro, double gauss)
- [PencilOfRays lens design forms](https://www.pencilofrays.com/lens-design-forms/), interesting but too advanced for me; I didn't know a lot of the attributes of my budget elements which I needed to fill out the spreadsheet
- formulas for lens combination and back focal distance [here](https://jackwestin.com/resources/mcat-content/geometrical-optics/combination-of-lenses) and [here](https://physics.stackexchange.com/questions/800901/geometric-optics-multiple-lens-focal-length-calculation)
- [Optical aberrations explained](https://www.eckop.com/resources/optics/aberrations/)
- [Lensmaker's equation on wikipedia](https://en.wikipedia.org/wiki/Lens#Lensmaker%27s_equation)
- [M57 adapters for Bronica from RAF Camera](https://rafcamera.com/searchanise/result?q=bronica%20m57)
