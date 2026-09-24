import { gzipSync } from "node:zlib";
import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";

// Comprueba que el JS inicial (entrada + el código de la ruta /torre) no supera el
// límite de gzip (NFR §7: 150 KB). El mapa MapLibre NO cuenta: carga en diferido
// después del primer pintado.
// Uso: node scripts/check-bundle-size.mjs  (tras `npm run build`)
// Variable BUNDLE_LIMIT_KB para subir el límite (con justificación en el PR).

const LIMIT_KB = Number(process.env.BUNDLE_LIMIT_KB ?? 150);
const LIMIT = LIMIT_KB * 1024;

// 1. Entrada: el fichero que referencia dist/index.html (no adivinar por glob).
const html = readFileSync("dist/index.html", "utf8");
const m = /src="(\/assets\/index-[^"]+\.js)"/.exec(html);
if (!m) {
  console.error("ERROR: no se encontró el script de entrada en dist/index.html");
  process.exit(1);
}
const entry = m[1].replace("/assets/", "");

// 2. Código de /torre: el chunk de la ruta (TorreDashboard) cuando tiene contenido.
const torre = readdirSync("dist/assets").find((f) => /^TorreDashboard-.*\.js$/.test(f));

const partes = [["entrada", entry]];
if (torre) partes.push(["/torre", torre]);

let total = 0;
for (const [label, file] of partes) {
  const bytes = readFileSync(join("dist/assets", file));
  const gz = gzipSync(bytes).length;
  total += gz;
  console.log(`${label} (${file}): ${(bytes.length / 1024).toFixed(1)} kB (${(gz / 1024).toFixed(1)} kB gzip)`);
}

console.log(`Total entrada + /torre: ${(total / 1024).toFixed(1)} kB gzip — límite ${LIMIT_KB} kB gzip`);

if (total > LIMIT) {
  console.error(
    `ERROR: entrada + /torre (${(total / 1024).toFixed(1)} kB gzip) supera el límite de ${LIMIT_KB} kB gzip. ` +
      `Carga diferida (lazy import tras el primer pintado) o sube BUNDLE_LIMIT_KB con justificación en el PR.`,
  );
  process.exit(1);
}
