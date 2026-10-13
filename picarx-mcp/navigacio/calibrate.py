"""Camera calibration from the /calib checkerboard images (9x6 inner corners).
Output: camera_matrix (fx,fy,cx,cy) + dist coeffs + reprojection error.
The square size does not affect the intrinsics (only the scale)."""
import glob, json
import cv2
import numpy as np

PATTERN = (9, 6)          # inner corners
SQUARE_MM = 12.5          # square size; irrelevant for intrinsics
IMG_W, IMG_H = 2304, 1296  # IMX708 Wide, native binned mode (after the camera swap)

objp = np.zeros((PATTERN[0] * PATTERN[1], 3), np.float32)
objp[:, :2] = np.mgrid[0:PATTERN[0], 0:PATTERN[1]].T.reshape(-1, 2) * SQUARE_MM

objpoints, imgpoints, used = [], [], []
files = sorted(glob.glob("calib/*.jpg"))
for f in files:
    img = cv2.imread(f)
    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    ok, corners = cv2.findChessboardCornersSB(g, PATTERN)
    if ok:
        objpoints.append(objp)
        imgpoints.append(corners)
        used.append(f.split("/")[-1])

print(f"checkerboard found: in {len(used)}/{len(files)} images")
if len(used) < 8:
    print("TOO FEW good images (<8) — need more/better images")
    raise SystemExit(1)

ret, K, dist, rvecs, tvecs = cv2.calibrateCamera(
    objpoints, imgpoints, (IMG_W, IMG_H), None, None)

# per-image reprojection error
errs = []
for i in range(len(objpoints)):
    proj, _ = cv2.projectPoints(objpoints[i], rvecs[i], tvecs[i], K, dist)
    errs.append(cv2.norm(imgpoints[i], proj, cv2.NORM_L2) / len(proj))
mean_err = float(np.mean(errs))

fx, fy, cx, cy = K[0, 0], K[1, 1], K[0, 2], K[1, 2]
hfov = np.degrees(2 * np.arctan(IMG_W / (2 * fx)))
vfov = np.degrees(2 * np.arctan(IMG_H / (2 * fy)))

print(f"\n=== CAMERA RECIPE ({IMG_W}x{IMG_H}) ===")
print(f"fx={fx:.1f}  fy={fy:.1f}  cx={cx:.1f}  cy={cy:.1f}")
print(f"dist={dist.ravel().round(4).tolist()}")
print(f"reprojection error (mean): {mean_err:.3f} px  (good: <0.5)")
print(f"estimated HFOV={hfov:.1f}°  VFOV={vfov:.1f}°")

out = {
    "_source": "calibrate.py, 9x6 checkerboard, IMX708 Wide, lens_position=1.0 (LOCKED focus!)",
    "image_size": [IMG_W, IMG_H],
    "camera_matrix": K.tolist(),
    "dist_coeffs": dist.ravel().tolist(),
    "fx": fx, "fy": fy, "cx": cx, "cy": cy,
    "hfov_deg": hfov, "vfov_deg": vfov,
    "reproj_err_px": mean_err,
    "n_images_used": len(used),
}
with open("camera_calib.json", "w", encoding="utf-8") as fh:
    json.dump(out, fh, indent=2, ensure_ascii=False)
print("\nsaved: camera_calib.json")
