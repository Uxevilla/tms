// Captura /viajes en tema claro y oscuro para el informe de la Fase 4.
const { chromium } = require("@playwright/test");

(async () => {
  const BASE = process.env.E2E_BASE_URL ?? "http://localhost:8080";
  const browser = await chromium.launch();
  const ctx = await browser.newContext({
    viewport: { width: 1440, height: 900 },
  });
  // Token del admin (storageState simplificado).
  await ctx.addInitScript((t) => localStorage.setItem("tms_jwt", t), process.env.TMS_TOKEN ?? "");
  const page = await ctx.newPage();

  async function shot(tema, out) {
    await page.goto(`${BASE}/viajes`);
    await page.getByRole("button", { name: /Nuevo viaje/i }).waitFor({ timeout: 20000 });
    if (tema === "oscuro") {
      await page.evaluate(() => document.documentElement.classList.add("dark"));
    }
    await page.waitForTimeout(1200);
    await page.screenshot({ path: out, fullPage: false });

    // Abrir el Sheet de nuevo viaje y capturar.
    await page.getByRole("button", { name: /Nuevo viaje/i }).click();
    await page.getByPlaceholder("Buscar cliente…").waitFor({ timeout: 8000 });
    await page.waitForTimeout(800);
    await page.screenshot({ path: out.replace(".png", "-sheet.png"), fullPage: false });
  }

  await shot("claro", "/tmp/viajes-claro.png");
  await shot("oscuro", "/tmp/viajes-oscuro.png");

  await browser.close();
  console.log("capturas OK");
})();
