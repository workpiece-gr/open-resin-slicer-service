from pathlib import Path

# Small 10 mm cube. The SLA quality profile elevates the object 5 mm and enables
# medium supports, so even this benign manifold exercises the support/slice path.
vertices = {
    "000": (0, 0, 0),
    "100": (10, 0, 0),
    "110": (10, 10, 0),
    "010": (0, 10, 0),
    "001": (0, 0, 10),
    "101": (10, 0, 10),
    "111": (10, 10, 10),
    "011": (0, 10, 10),
}

triangles = [
    ("000", "110", "100"), ("000", "010", "110"),  # bottom
    ("001", "101", "111"), ("001", "111", "011"),  # top
    ("000", "100", "101"), ("000", "101", "001"),  # front
    ("010", "011", "111"), ("010", "111", "110"),  # back
    ("000", "001", "011"), ("000", "011", "010"),  # left
    ("100", "110", "111"), ("100", "111", "101"),  # right
]

lines = ["solid workpiece_acceptance_cube"]
for a, b, c in triangles:
    lines.extend([
        "  facet normal 0 0 0",
        "    outer loop",
        f"      vertex {' '.join(map(str, vertices[a]))}",
        f"      vertex {' '.join(map(str, vertices[b]))}",
        f"      vertex {' '.join(map(str, vertices[c]))}",
        "    endloop",
        "  endfacet",
    ])
lines.append("endsolid workpiece_acceptance_cube")

output = Path("acceptance-cube.stl")
output.write_text("\n".join(lines) + "\n", encoding="ascii")
print(output)
