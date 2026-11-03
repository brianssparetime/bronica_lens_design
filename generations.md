# Lens Design Generations

## v1 — Initial Petzval
Three-element Petzval picked by focal length. No conjugate math, no achromat.

## v2 — Rear swap
Swapped rear negatives. Same architecture, same aberration problems.

## v3 — More rear swaps, late BFD audit
More exploration in the same shape. A retrospective conjugate check showed
these geometries sit near the mirror floor.

## v4 — Achromat in, throat rule in
Cemented doublet in the middle to fix chromatic. M57 throat clearance joined
the hard-constraint list.

## v5 — Conjugate-equation BFD
Made real BFD math the standard. Throat and mirror became pass/fail.

## v6 — Exhaustive sweep
Stopped picking by hand. Swept the purchased inventory for compliant
combinations.

## v7 — Targeted purchase experiment
Tried a larger positive to see if expanding the search space helped. It
didn't. Changed sourcing policy: buy to fill specific sweep gaps, not
speculatively.

## v8 — Petzval ceiling
Pinned the compliant-swirl ceiling for Petzval-order designs. No more
headroom on that architecture with purchased stock.

## v9 — Out of Petzval
Reordered the same elements into retrofocus and Cooke. Same swirl math,
different BFD geometry — much higher compliant swirl, an order of magnitude
more spacing tolerance.

## v10 — Re-rate, broaden, refine
Fixed the evaluator's achromat math, updated thickness data, re-rated
everything. Most v9 designs fell out. Broadened the search to Cookes and a
wider retrofocus family. Late: worked out that moving the achromat to the
middle of a retrofocus, not the rear, cuts spherical for free and opened a
sharpness-vs-swirl trade.

## v11 — Promote and explore around the v10 winners
Copied the strongest v10s as baselines and ranged across the inventory
looking for shorter focal length with brighter corners and a sharper
center. The singlet-retrofocus family caps below the sharpness target
without either stopping down or sourcing a large negative achromat. The
fast variants land focal length and brightness; the nominally-sharp
variants get there by closing the aperture, which is a different lens,
not a better one.

## v12 — Field shape becomes a first-class target
Added field-shape requirements to the spec and a Seidel-astigmatism
diagnostic to the evaluator, then swept the inventory looking for
designs where Petzval and astigmatism oppose -- the bowl signature
described in insights section five. Stop position turned out to be the
dominant lever: the same elements that read as smooth Petzval parabolas
under the auto-stop swing into bowl-direction astigmatism when the stop
is pushed deep into a rear gap, and the wider that gap the stronger the
bowl. The first cut had geometrically impossible stops -- the spacing
diagram caught it -- which surfaced a missing physical-fit check in the
evaluator. Once that gate was added and spacings widened to actually
accommodate the deep stops, multiple candidates landed past the
preferred bowl threshold and one reached far past it. None reach the
optimal plateau radius; the inventory's negative powers are still too
strong for a true flat plateau without sourcing a large-diameter
negative achromat.

## v13 — Flange clearance, sharpness, and the cancellation rule
v12c was promoted to v13a as the baseline, then audited against the
DESIGN-REQS hard rule that the stop slot must clear the flange plane
by 2.5mm. v12c failed it: its stop slot straddled the flange. Pushing
the stop forward at fixed elements shallowed the bowl, so the
chemistry-preserving fix was to widen d2 instead (v13b). The bigger
discovery was that pairing a very weak NMN front (L11018 -210 or
L14234 -207) with a strong positive rear (big-PCX 185/127mm) at the
right spacings produces near-perfect third-order spherical
cancellation -- the singlet-retrofocus family broke the
sharp-radius=0 ceiling for the first time (v13d at 18% sharp, v13e at
17.7%) while still showing the bowl signature. The trade is swirl:
the cancellation requires weak front powers, so swirl drops from
138% (v13a) toward the 105-118% band. v13c picks the other corner of
the tradeoff -- L14234/L14929/L13088 -- giving the lowest spacing
sensitivity yet (0.21 mm/mm) at near-target EFL with bowl preserved
but no sharp center.

Took a closer look at big-PCX after v13e. Its bare diameter does no
optical work in our layouts because the limiting aperture sits at the
interior achromat, not the rear; the 127mm is glass to look at, not
glass to use. Equivalent crown-glass PCX in the right focal length
is widely sold at 50mm in the K9 family on eBay and Amazon. Walking
through the spherical-cancellation arithmetic explicitly produced a
one-line rule: given a singlet negative front and a PCX rear of
standard shape, the rear focal length that cancels third-order
spherical equals the front's focal length divided by the ratio of
their shape factors -- a function of glass and bending alone, not of
spacings or stop. For our purchased negatives, the rule selects a
tight set of catalog focal lengths: a 175mm rear for L11018, a 150mm
for L7325, a 125mm for L14801, and a 200mm for a deeper-bowl variant.
Filled out the generation with these (v13f at 150mm, v13g at 175mm,
v13h at 200mm, v13i at 125mm) and bought the matching K9 PCX stock.
The 175mm design (v13g) lands at the project sweet spot, with every
priority criterion satisfied at once and spacing sensitivity back at
v12c levels -- it replaces v13d/e as the headline result and v13a as
the project's reference build.

## v14 — Corrected swirl, a decomposed metric, and the four-element test

v13f and v13g carried straight into the generation as v14a and v14b,
unchanged. The balanced retrofocus pair stayed the reference, and nothing
displaced it.

The generation's real work was conceptual. A re-derivation of swirl
direction, checked against optics sources and the project's own swirl
writeup, found the old astigmatic-sign convention inverted: the bowl-shaped
designs the project had been calling reverse-swirl are in fact
background-swirl, and a flat sharp center and swirling edges come from the
same astigmatism rather than excluding each other. The single
Dallmeyer-percentage swirl figure gave way to a decomposition that scores
direction, field-curvature depth, the medial plateau, and coma separately,
with mechanical vignetting acknowledged as an often-dominant effect the
thin-lens model cannot reach.

The design requirements were rewritten around that understanding, folding
swirl and center sharpness into one priority that names the tension between
them. Depth of swirl and width of the flat center pull against each other,
and the goal is the best joint point rather than the extreme of either.

The sweep then tested whether more elements could break that tension, using
the unused barrel length for four-element retrofocus stacks. They could not.
A fourth element only slides a design along the same depth-versus-plateau
frontier the three-element family already occupies, and it spends spacing
tolerance on the extra gap. The exploration confirmed v14b sits at a
genuinely good point rather than an artifact of the old metric.

One four-element design earned a place as v14c despite not competing: a
double-negative form that reaches a flat, sharp center with strong swirl
orientation but shallow swirl depth. It records the opposite corner of the
frontier, a sharp flat-field portrait with gentle swirl, for a future where
that character is wanted.

## v15 — Tangential swirl, from corrected coma

Film from v14a came back as radial tie-dye, not the tangential swirl the metric
promised, and the whole v14 family does the same. Coma is the reason. The strong
negative singlet that sets the swirl direction also carries the dominant coma,
and coma smears the corner radially, overriding the astigmatic sign the score
trusted. Direction is a contest between astigmatism and coma, not a property of
astigmatism alone.

The fix lives at the element. One negative element sources both the tangential
astigmatism and the coma, so a negative singlet cannot give the first without
the second. A negative cemented achromat carries the same negative power with
almost no spherical, and so almost no coma: direction without the contaminant.
Make every element an achromat and the spherical vanishes everywhere, so the
center stays sharp by absence rather than by the singlet cancellation v14 used.

v15a is that lens. A negative achromat in front, two positive achromats behind,
stop between them, a Petzval form rather than the v14 retrofocus. It holds
v14a's field depth and flat center while turning the corner smear from radial to
tangential. The two share no glass; every singlet became an achromat.

The evaluator learned to read the contest, scoring swirl against tie-dye by the
ratio of coma to astigmatism, and the v15 sweep gates its swirl figure on that
ratio so a deep tie-dye no longer ranks as swirl.

Depth and speed now rest on the positive achromats, with the negative carrying
only direction. Sourcing better glass buys little; the next test is a roll of
film.

## v16 — Moving the crossing to the frame edge

Film from v15a showed the rough field is not one thing but three: a sharp
center, a ring where the background smears into tangential arcs, and corners
past a crossing radius where the smear turns radial again. At v15a's focal
length the frame reaches past that crossing into the radial corners, so the
picture reads as tie-dye even though the swirl is genuinely present in the
ring. The generation's goal is to move the crossing outward until the ring's
edge meets the midpoints of the frame sides, a circle inscribed in the square,
leaving a wide sharp center and only the four corners radial.

The crossing sits beyond the third-order model. A higher-order astigmatism
reverses the direction at the edge, and it has no closed form in the thin-lens
math, so where the crossing falls can be read only from film. What the model
does reach are the two levers that move it. Lengthening the lens projects the
crossing outward against a fixed frame, but reaching a longer focal length with
this glass flattens the field, and the swirl shallows with it. Deepening the
astigmatism, with the stop carried far back, pushes the crossing out at a fixed
focal length and keeps the full depth, at the price that the model cannot say
how far the astigmatism moves the crossing.

Three designs take the corners of that trade. v16a lengthens to the far end of
the acceptable range and flattens the field until the swirl thins below the
floor. v16b holds v15a's focal length and curved field and cranks the
astigmatism alone, betting the stop does the work. v16c works both levers at
once, a longer focal length and a strong astigmatism together, which buys a
wide sharp center and pushes the crossing furthest of the three, at the cost of
a heavy modeled vignette that earlier rolls suggest does not print.

v16c mostly reuses proven glass. L1198, its front negative, is v15a's front;
L11561, its middle positive, is v14a's middle; L14929, its rear positive, is
new to the project.

The crossing shows only on film, so the generation ends at a roll rather than a
figure. v16c is the joint attempt at the inscribed circle, and v16b is the
control beside it, holding everything but the astigmatism fixed against the
already-shot v15a so the astigmatism lever can be read alone. A third lever
stays untried: reduce the edge astigmatism itself rather than outrun it, which
would carry the crossing outward without flattening the field, and which waits
on this roll to calibrate.

v16d is v16c after the build: the same optics with a measured infinity-focus
offset applied, shifting the core toward the film so infinity lands at zero
helicoid extension.

## A note on BFD and back clearance

Everything above this line was written when "BFD" in the project meant
the distance from the rearmost glass to the film -- a physical
clearance. The term has since been split to match convention: BFD now
means the optical rear-vertex-to-film distance, and a new back
clearance carries the nearest-glass-to-film dimension that the build
and the body constraints actually depend on. For every design above,
the two are the same number, since each rear element is convex toward
the film; read their BFD figures as back clearance.
