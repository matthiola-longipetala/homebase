// =====================================================================
//  ReSpeaker XVF3800 USB 4-Mic Array - mount / standoff stand
//  Parametric OpenSCAD master. Units: mm.
//  Render: F6, then Export as STL.  Every dimension can be changed above.
// =====================================================================

/* [Main dimensions] */
disc_d        = 100;
disc_clear    = 0.6;   // fit clearance around the disc (looser=larger)
board_t       = 1.8;   // PCB thickness (estimate)
total_h       = 95;    // height of the disc seat from the bottom of the base (9-10cm)

/* [Base - the legs stay within this 80x60] */
base_x        = 80;
base_y        = 60;
base_t        = 4;
base_fillet   = 6;
screw_d       = 3.4;   // M3 through hole
screw_pair_dx = 40;    // <<< SPACING OF THE 2 FRONT SCREW POSITIONS - MEASURE IT ON THE ROBOT! >>>
corner_holes  = true;  // +4 corner holes for more mounting options

/* [Ring (seat shelf) + retaining lip] */
shelf_outer   = 54;
shelf_inner   = 44;
shelf_t       = 4;
lip_h         = 4;     // height of the retaining lip above the disc
lip_wall      = 3;
cable_gap_deg = 42;    // cable gap in the lip (USB-C side)
clip_count    = 3;     // upper retaining tabs so the disc does not pop out

/* [Tripod legs] */
hub_r         = 16;
hub_t         = 9;
leg_r         = 6;
leg_base_r    = 28;
leg_top_r     = 12;
leg_angles    = [90, 210, 330];

$fn = 96;
// ---------------------------------------------------------------------
board_r   = disc_d/2;
lip_inner = board_r + disc_clear;
lip_outer = lip_inner + lip_wall;
shelf_bot = total_h - shelf_t;

module rounded_base() {
  difference() {
    hull() for (sx=[-1,1], sy=[-1,1])
      translate([sx*(base_x/2-base_fillet), sy*(base_y/2-base_fillet), 0])
        cylinder(r=base_fillet, h=base_t);
    for (sx=[-1,1])
      translate([sx*screw_pair_dx/2, 0, -1]) cylinder(d=screw_d, h=base_t+2);
    if (corner_holes) for (sx=[-1,1], sy=[-1,1])
      translate([sx*(base_x/2-8), sy*(base_y/2-8), -1]) cylinder(d=screw_d, h=base_t+2);
  }
}

module leg(ang) {
  p0 = [leg_base_r*cos(ang), leg_base_r*sin(ang), base_t*0.5];
  p1 = [leg_top_r*cos(ang),  leg_top_r*sin(ang),  shelf_bot - hub_t*0.4];
  hull() { translate(p0) sphere(leg_r); translate(p1) sphere(leg_r); }
  translate([p0[0], p0[1], 0]) cylinder(r=leg_r+2.5, h=base_t*1.6); // base collar
}

module ring(ri, ro, h) {            // solid ring
  rotate_extrude() translate([ri,0]) square([ro-ri, h]);
}

module lip_with_gap() {
  rotate([0,0, cable_gap_deg/2])
    rotate_extrude(angle = 360 - cable_gap_deg)
      translate([lip_inner, 0]) square([lip_wall, lip_h]);
}

module clips() {
  for (i = [0 : clip_count-1]) {
    ang = 150 + i*120;            // avoiding the cable gap (0 degrees)
    rotate([0,0,ang]) translate([lip_inner-1, -4, 0]) cube([5, 8, 2]);
  }
}

union() {
  rounded_base();
  for (a = leg_angles) leg(a);
  // hub
  translate([0,0, shelf_bot - hub_t + 1]) cylinder(r=hub_r, h=hub_t);
  // spokes hub -> ring
  for (ang=[30,150,270]) rotate([0,0,ang])
    translate([hub_r-3, -4, shelf_bot]) cube([shelf_inner-hub_r+6, 8, shelf_t]);
  // seat ring
  translate([0,0, shelf_bot]) ring(shelf_inner, shelf_outer, shelf_t);
  // retaining lip with cable gap
  translate([0,0, total_h]) lip_with_gap();
  // upper tabs
  translate([0,0, total_h + lip_h - 1]) clips();
}
