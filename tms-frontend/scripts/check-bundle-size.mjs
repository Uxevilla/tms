import { gzipSync } from "node:zlib";
import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";

// Comprueba que el JS inicial (entrada) no supera el límite de gzip (NFR §7: 150 KB).
// Uso: node scripts/check-bundle-size.mjs  (tras `npm run build`)
// Variable BUNDLE_LIMIT_KB para subir el límite (con justificación en el PR).

const LIMIT_KB = Number(process.env.BUNDLE_LIMIT_KB ?? 150);
const LIMIT = LIMIT_KB * 1024;
const dir = "dist/assets";

const entry = readdirSync(dir).find((f) => /^index-.*\.js$/.test(f));
if (!entry) {
  console.error("ERROR: no se encontró el fichero de entrada index-*.js en dist/assets");
  process.exit(1);
}

const bytes = readFileSync(join(dir, entry));
const gz = gzipSync(bytes).length;
console.log(
  `${entry}: ${(bytes.length / 1024).toFixed(1)} kB (${(gz / 1024).toFixed(1)} kB gzip) — límite ${LIMIT_KB} kB gzip`,
);

if (gz > LIMIT) {
  console.error(
    `ERROR: el JS inicial (${(gz / 1024).toFixed(1)} kB gzip) supera el límite de ${LIMIT_KB} kB gzip. ` +
      `Carga diferida (lazy import tras el primer pintado) o sube BUNDLE_LIMIT_KB con justificación en el PR.`,
  );
  process.exit(1);
}
