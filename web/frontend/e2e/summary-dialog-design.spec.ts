import { expect, test } from "@playwright/test";

import { installApiMocks, setLocalAuthState } from "./fixtures/api-mocks";

const summaries = [
  {
    id: "summary-1",
    transcription_id: "1",
    template_id: "1",
    template_name: "Meeting Notes",
    model: "qwen2.5:7b",
    content: "## Key Topics Discussed\n\n- Personal reflections and emotions\n- Health and spirituality\n\n## Decisions Made\n\n- Continue the conversation next week.",
    created_at: "2026-09-24T15:00:00Z",
    updated_at: "2026-09-24T15:00:00Z",
  },
];

test.beforeEach(async ({ page }) => {
  await setLocalAuthState(page);
  await page.addInitScript(() => {
    document.documentElement.classList.add("dark");
  });
  await installApiMocks(page);
  await page.route("**/api/v1/transcription/1/summaries", (route) =>
    route.fulfill({ json: summaries }),
  );
});

test("uses restrained gold accents and balanced summary controls", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto("/audio/1");

  const selectedRecording = page.locator('button[aria-current="page"]');
  const selectedColors = await selectedRecording.evaluate((element) => {
    const style = getComputedStyle(element.parentElement!);
    const root = getComputedStyle(document.documentElement);
    const resolveColor = (value: string) => {
      const swatch = document.createElement("span");
      swatch.style.color = value;
      document.body.appendChild(swatch);
      const color = getComputedStyle(swatch).color;
      swatch.remove();
      return color;
    };
    return {
      background: style.backgroundColor,
      elevated: resolveColor(root.getPropertyValue("--bg-elevated").trim()),
      title: getComputedStyle(element.querySelector("span")!).color,
      primaryText: resolveColor(root.getPropertyValue("--text-primary").trim()),
    };
  });
  expect(selectedColors.background).toBe(selectedColors.elevated);
  expect(selectedColors.title).toBe(selectedColors.primaryText);
  await page.screenshot({ path: "/tmp/plume-sidebar-selection.png", fullPage: true });

  await page.getByRole("button", { name: "AI Summary" }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog.getByRole("heading", { name: "Summaries" })).toBeVisible();
  await expect(dialog.getByText("Meeting Notes", { exact: true }).last()).toBeVisible();

  const newSummary = dialog.getByRole("button", { name: "New Summary" });
  const buttonStyle = await newSummary.evaluate((element) => {
    const style = getComputedStyle(element);
    return {
      height: style.height,
      backgroundImage: style.backgroundImage,
      boxShadow: style.boxShadow,
    };
  });
  expect(buttonStyle.height).toBe("32px");
  expect(buttonStyle.backgroundImage).toBe("none");
  expect(buttonStyle.boxShadow === "none" || !buttonStyle.boxShadow.includes("0.")).toBe(true);

  const toolbar = dialog.getByRole("button", { name: "Regenerate" }).locator("../..");
  const toolbarPadding = await toolbar.evaluate((element) => {
    const style = getComputedStyle(element);
    return { top: style.paddingTop, bottom: style.paddingBottom };
  });
  expect(toolbarPadding.top).toBe(toolbarPadding.bottom);

  await page.screenshot({ path: "/tmp/plume-summary-desktop.png", fullPage: true });

  await page.setViewportSize({ width: 390, height: 844 });
  await expect(dialog).toBeVisible();
  await page.screenshot({ path: "/tmp/plume-summary-mobile.png", fullPage: true });
});
