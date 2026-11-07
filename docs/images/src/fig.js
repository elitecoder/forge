// Small string builders for the Forge README figures. Each page loads kit.css,
// dark.js, and this file, then fills <svg id="s"> with D.* calls.
const D = {
  box: (x, y, w, h, c = "f") =>
    `<rect class="${c}" x="${x}" y="${y}" width="${w}" height="${h}" rx="4"/>`,
  t: (x, y, s, c = "", a = "middle") =>
    `<text class="${c}" x="${x}" y="${y}" text-anchor="${a}">${s}</text>`,
  dot: (x, y, r = 7, c = "f") => `<circle class="${c}" cx="${x}" cy="${y}" r="${r}"/>`,
  head: (x, y, fx, fy, c = "") => {
    const a = Math.atan2(y - fy, x - fx), L = 11, s = 0.48;
    const p = (k) => `${(x - L * Math.cos(a + k)).toFixed(1)} ${(y - L * Math.sin(a + k)).toFixed(1)}`;
    return `<path class="${c}" d="M${p(-s)} L${x} ${y} L${p(s)}"/>`;
  },
  // Polyline through pts with an arrowhead at the last point.
  line: (pts, c = "", arrow = true) => {
    const d = "M" + pts.map((p) => p.join(" ")).join(" L");
    const [a, b] = [pts[pts.length - 2], pts[pts.length - 1]];
    return `<path class="${c}" d="${d}"/>` + (arrow ? D.head(b[0], b[1], a[0], a[1], c.replace(/\bd\b/, "")) : "");
  },
  // Cubic curve from p0 to p3 with an arrowhead at p3.
  curve: (p0, p1, p2, p3, c = "", arrow = true) =>
    `<path class="${c}" d="M${p0} C${p1} ${p2} ${p3}"/>` +
    (arrow ? D.head(p3[0], p3[1], p2[0], p2[1], c.replace(/\bd\b/, "")) : ""),
  draw: (parts) => { document.getElementById("s").innerHTML = parts.join(""); },
};
