import { expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders } from "./utils/render";
import { stubFetch } from "./utils/fetchStub";

const { uploadWithProgress } = vi.hoisted(() => ({ uploadWithProgress: vi.fn() }));
vi.mock("@/lib/upload", () => ({ uploadWithProgress }));

it("reloads J-Lens samples after audio and transcript uploads", async () => {
  const formattedName = "custom:session:speech";
  const metadataPath = `/${encodeURIComponent(formattedName)}/metadata`;
  let hasManifest = false;
  const dataset = {
    dataset_name: "speech",
    formatted_name: formattedName,
    created_at: "2026-01-01T00:00:00Z",
    session_id: "session",
    files: [],
    total_files: 0,
  };
  const api = stubFetch({
    "GET /models": { json: [] },
    "GET /models/jacobian-lenses/whisper-base": { json: [] },
    "GET /upload/dataset/list": { json: { datasets: [dataset] } },
    "GET /common-voice/metadata": { json: [] },
    [`GET ${metadataPath}`]: () => ({
      json: hasManifest
        ? [
            { filename: "one.wav", transcript: "one" },
            { filename: "two.wav", transcript: "two" },
          ]
        : [],
    }),
    "POST /upload/dataset/speech/manifest": () => {
      hasManifest = true;
      return { json: { dataset_name: formattedName, manifest: { pair_count: 2, matched_audio_count: 2 } } };
    },
  });
  uploadWithProgress.mockResolvedValue({
    dataset_name: formattedName,
    uploaded_files: [{ original_filename: "one.wav" }, { original_filename: "two.wav" }],
  });

  const { default: JacobianLensLab } = await import("@/pages/JacobianLensLab");
  renderWithProviders(<JacobianLensLab />, { route: "/jacobian-lens" });
  const user = userEvent.setup();
  await user.click(screen.getByRole("button", { name: /Manage Datasets/ }));
  await user.click(screen.getByRole("tab", { name: /Upload Files/ }));
  await user.selectOptions(screen.getByLabelText("Select Dataset"), "speech");
  await user.upload(screen.getByLabelText("Audio Files"), [
    new File(["one"], "one.wav", { type: "audio/wav" }),
    new File(["two"], "two.wav", { type: "audio/wav" }),
  ]);
  await user.click(screen.getByRole("button", { name: /^Upload Files$/ }));
  await waitFor(() => expect(api.callsFor(`GET ${metadataPath}`)).toHaveLength(1));
  expect(screen.getByText(/0 transcript-bearing samples available/)).toBeInTheDocument();

  await user.upload(screen.getByLabelText(/Transcript Manifest/),
    new File(["filename,transcript\none.wav,one\ntwo.wav,two\n"], "metadata.csv", { type: "text/csv" }));
  await user.click(screen.getByRole("button", { name: /Upload metadata.csv/ }));
  await waitFor(() => expect(api.callsFor(`GET ${metadataPath}`)).toHaveLength(2));
  expect(await screen.findByText(/2 transcript-bearing samples available/)).toBeInTheDocument();
  await user.click(screen.getAllByRole("button", { name: "Close" })[0]);
  await user.click(screen.getByRole("button", { name: /Select samples/ }));
  expect(screen.getByRole("button", { name: /Fit with 2 samples/ })).toBeEnabled();
});
