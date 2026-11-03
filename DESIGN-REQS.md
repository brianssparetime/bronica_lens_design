# Design requirements


## Goal:

Produce a lens design that will work on a Bronica S2a camera.


## hard requirements:

These are pass-fail, and failing designs are not physically viable or worth discussion

 - Back clearance and element diameter:

    - Axial position `z` is measured from the camera flange (`z = 0`), with `+z`
    toward the subject, taken at the element's deepest (lower) face.

      - For `z > 30 mm`: maximum element diameter is unconstrained
      - For `30mm  >=  z > −7.3 mm`: maximum element diameter is **52.7 mm**.
      - For `-7.3 mm >= z > −20.3 mm`: maximum element diameter is **50.45 mm**.
      - For `-20.3 >= z > −30.0 mm`: maximum element diameter is **45.7 mm**.
      - no part of any element may sit further in thatn -30mm regardless of size.

      - note also that lens thickness must be taken into account - if the center lies outside the constricted zone, but the thickness extends into it, that's no good.

    - recessesing elements (z negative) is perfectly fine, as long as the diameter-per-depth requirements above is satisfied, and may contribute to the low priority goal of less protrusion from the camera (see below).

Element spacing:
 - The element gap between two adjacent elements is: center-to-center distance minus half the thickness of each element.
   gap = d - (t1/2 + t2/2)
 - when the thickness of an element is unknown or null, assume 10mm
 - preferred: gap >= 5mm
 - minimum acceptable: 2mm (only if max_thickness is known for both elements)
 - Do not offer designs with 0 or negative gap.


 The stop:
  - The stop must not be within an element.  
    - Assume a 2mm thickness for the stop, and 1mm clearance from the nearest lens element on each side  
    - look at the max_thickness for lens elements when computing this clearance
  - the stop must not be too close to the flange:  
    - if any part of the stop slot is within the range of 5mm (towards the lens) to -3.5 (towards the camera) (with flange at 0), the design is not valid
        - it is a soft low priority preference that the stop sit on the positive side (outside the flange) rather than inside, but this may be compromised for better optical performance
    - If the stop is too close to the flange/camera mount, it  presents a problem for manufacture - the half slot for the stop weakens the housing at a critical point and interferes with the flat seating of the lens's flange flange against the camera's flange surface, and the ability to remove the stop is blocked by the lens-mount-ring.



## priority criteria:



(prio 1) Petzval-portrait character: strong background swirl carried on a
flat, sharp center plateau.

This is the headline goal, combining what were previously separate swirl and
center-sharpness requirements. It is a balance, not a maximization. Swirl
depth and the width of the sharp center plateau pull against each other: a
deeper-curving field swirls harder but shrinks the flat center. The target is
the best joint point, not the extreme of either.

Direction (required). The corner bokeh must smear tangentially, the
background SWIRL orientation, not radial tie-dye. In evaluate.py terms,
swirl_astig > 0. A design whose astigmatism points the other way (tie-dye) or
vanishes (near-isotropic) does not qualify as swirly, however deeply its
field curves.

Swirl strength (depth, gated by direction). Measured as
swirl_FoM = swirl_pct * direction, that is, field-curvature depth gated
behind the direction requirement above, so a deep but tie-dye design scores
low by construction.
 - optimal:    about 125-150% of a dallmeyer
 - preferred:  more than a dallmeyer
 - acceptable: even to dallmeyer

Center plateau (flat region of the medial surface). Radii are a fraction of
the frame half-diagonal (39.6mm for the 56mm x 56mm S2a frame). "Approximate
flatness" means the medial-surface defocus produces a blur no larger than the
on-axis sharpness threshold (SHARP_COC_THRESHOLD_MM, currently 0.05mm) at the
operating f-number.
 - optimal:    plateau extends to 40% of half-diagonal (~16mm)
 - preferred:  plateau extends to 30% (~12mm)
 - acceptable: plateau extends to 20% (~8mm)

Center sharpness wide open (on-axis, includes spherical):
 - preferred:  center 1/2 to 1/3 (diagonal) reasonably sharp of a 6x6
               (56mm x 56mm) crop of the image circle
 - acceptable: a bit soft in the center wide open but sharpens, while keeping
               swirl, when stopped down; or center 1/3 to 1/4 sharp.

Edge curl beyond the plateau. The medial surface should curl monotonically
outward past the plateau radius, with growing defocus toward the corner. We
set no numeric target: the third-order Seidel approximation in evaluate.py
predicts a smooth parabolic curl that under-states the bowl's true edge
behavior, so any threshold would measure the wrong quantity. A design that
meets the plateau requirement and shows visible defocus in the outer 30% of
the field meets this criterion.

Astigmatic signature (descriptive, not a separate target). The canonical
swirl-plus-flat-center comes from Petzval and astigmatism opposing, A and P
opposite-signed with |A/P| > 0.05, which flattens the medial surface beyond
Petzval alone while sagittal and tangential diverge at the edge. The
direction term above already captures this (|A/P| > 0.05 corresponds to the
direction score saturating), so it needs no separate weighting.




(prio 2) Effective focal length:
 - optimal: about 100mm
 - preferred: wider than 100mm, 100-110mm
 - acceptable 110-135mm


(prio 2) Combined lens speed:
 - optimal:  f2.4
 - preferred:  f2 to f3.2
 - acceptable <= f4 and >= f2

(prio 3) Chromatic aberation (CA):
 - optimal:  little
 - preferred: little-to-moderate
 - acceptable:  moderate

## low priority criteria


All else equal, less spacing sensitivity (more tolerance for spacing error) is better.

All else equal, a lens that protudes from the camera less is better (but lens-spacing is more important).


