# LiDAR Acquisition & Delivery Specification — Rev C

**Castellan Grid Consulting — Vegetation Management & LiDAR Analytics Practice**
**Effective:** 2025-11 | **Owner:** R. Donnelly, VP Engineering | **Supersedes:** Rev B (2024-06)
**Applies to:** All corridor LiDAR acquisition vendors under master services agreements with Castellan

## 1. Purpose and scope

This specification governs airborne LiDAR acquisition and delivery for transmission-corridor vegetation management work performed by Castellan on behalf of its utility clients, principally in support of NERC FAC-003 vegetation clearance analysis. It defines the required data products, accuracy and density standards, delivery media and timing, and Castellan's acceptance QA procedure. Acquisition vendors are responsible for flight planning, sensor calibration, trajectory processing, and point-cloud classification to the classes listed in Section 2 before delivery.

## 2. Required data products

Each delivery shall include:

- **Classified point cloud** in LAS 1.4 format (point data record format 6 or higher), classified to ASPRS standard classes at minimum: ground (2), low/medium/high vegetation (3/4/5), building/structure (6), conductor — wire (14), transmission tower (15), and noise (7).
- **Smoothed best-estimate trajectory (SBET)** files for each flight line.
- **Survey control report** documenting base stations, check points, and residuals.
- **Flight report** covering dates, sensor, altitude AGL, planned vs. achieved coverage, and weather holds.
- Orthoimagery where contracted (optional line item; not all clients purchase imagery).

## 3. Density and accuracy standards

- Nominal point density: **≥ 30 points/m²** aggregate within the corridor envelope; ≥ 45 points/m² within 25 ft of the centerline.
- Vertical accuracy: **RMSEz ≤ 5 cm** on hard-surface check points; ≤ 10 cm in vegetated terrain.
- Conductor classification completeness: ≥ 98% of visible span length classified to class 14. Misclassified conductor points are grounds for rejection of the affected tiles.

## 4. File sizing and tiling

Deliveries shall be tiled on the standard 1-km corridor tile grid issued with each task order. For planning purposes, complete deliverable bundles average 20 GB per line-mile (range 15–25 GB depending on point density and corridor width): raw swath plus classified LAS at the specified density, flight trajectory files, corridor ortho imagery at 5 cm GSD, and derivative rasters (canopy height model, bare-earth DEM). Classified LAS alone typically runs 2–4 GB per line-mile; the imagery and derivative products dominate the delivered volume. Vendors shall not compress LAS to LAZ for primary delivery unless the task order states otherwise; LAZ copies may be included as a courtesy.

## 5. Delivery medium and timing

Primary delivery medium: encrypted USB hard drives shipped via courier; upload to the CloudCrate account within 10 business days of receipt. Drives are addressed to the Denver office, attention Vegetation Management practice. Incoming drives are checked in on the drive log maintained on a whiteboard in the Denver plotter room; drives awaiting upload are staged in the locked media cabinet. Vendors shall retain a verified copy of each delivery until Castellan issues written acceptance.

Delivery is due no later than 20 business days after the final flight of each task order segment.

## 6. Acceptance QA

Castellan performs acceptance QA on each delivery before releasing the vendor invoice for payment:

1. **Coverage check** — delivered tile footprint vs. the task-order flight plan; any gap > 50 m of centerline triggers a refly request.
2. **Density verification** — density raster generated per tile; tiles below Section 3 minima are rejected.
3. **Classification spot-check** — a 5% random tile sample reviewed manually; ≥ 95% classification accuracy required on the sample.
4. **Control residuals** — check-point residuals recomputed against the survey control report.
5. **Conductor completeness** — span-by-span review of class 14 continuity in the clearance analysis software.

Rejected tiles must be corrected and redelivered within 10 business days; reflights are scheduled at the vendor's cost where acquisition itself is deficient.

## 7. Downstream use — FAC-003 clearance analysis

Accepted point clouds feed Castellan's clearance analysis workflow: vegetation-to-conductor distance computation at maximum operating temperature and blowout conditions, grow-in and fall-in hazard identification, and the client-facing clearance report used in NERC FAC-003 vegetation management program evidence. Classification quality directly limits the defensibility of these deliverables; QA shortcuts in Section 6 are not permitted under schedule pressure.

## 8. Security and handling

Drives shall be encrypted (AES-256) with passphrases transmitted separately from the shipment. Vendor-retained copies shall be destroyed on written acceptance, with destruction certified by email. Point-cloud data is client-confidential infrastructure information and shall not be stored on vendor systems outside the contracted processing environment.

---
*Rev history: Rev A (2022-03, initial); Rev B (2024-06, added conductor completeness standard); Rev C (2025-11, current — added encryption and upload-window requirements).*
