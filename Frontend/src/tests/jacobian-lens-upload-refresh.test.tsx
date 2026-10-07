import { beforeEach, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders } from "./utils/render";
import { stubFetch } from "./utils/fetchStub";

const { uploadWithProgress } = vi.hoisted(() => ({ uploadWithProgress: vi.fn() }));
vi.mock("@/lib/upload", async (importOriginal) => ({
  ...await importOriginal<typeof import("@/lib/upload")>(),
  uploadWithProgress,
}));

beforeEach(() => uploadWithProgress.mockReset());

it("retries a broken batch with the same upload ID", async () => {
  stubFetch({ "GET /upload/dataset/list": { json: { datasets: [{
    dataset_name: "speech", formatted_name: "custom:session:speech",
    created_at: "2026-01-01T00:00:00Z", session_id: "session", files: [], total_files: 0,
  }] } } });
  uploadWithProgress.mockRejectedValueOnce(new Error("connection reset"))
    .mockResolvedValueOnce({ dataset_name: "custom:session:speech", uploaded_files: [{ original_filename: "clip.flac" }] });
  const { CustomDatasetManager } = await import("@/components/dataset/CustomDatasetManager");
  renderWithProviders(<CustomDatasetManager />);
  const user = userEvent.setup();
  await user.click(screen.getByRole("button", { name: /Manage Datasets/ }));
  await user.click(screen.getByRole("tab", { name: /Upload Files/ }));
  await user.selectOptions(screen.getByLabelText("Select Dataset"), "speech");
  await user.upload(screen.getByLabelText("Audio Files"), new File(["audio"], "clip.flac", { type: "audio/flac" }));
  await user.click(screen.getByRole("button", { name: /^Upload Files$/ }));

  await waitFor(() => expect(uploadWithProgress).toHaveBeenCalledTimes(2), { timeout: 5000 });
  const firstBody = uploadWithProgress.mock.calls[0][1] as FormData;
  const retryBody = uploadWithProgress.mock.calls[1][1] as FormData;
  expect(retryBody).toBe(firstBody);
  expect(firstBody.get("upload_id")).toMatch(/[0-9a-f-]{36}/);
  expect(await screen.findByText("clip.flac")).toBeInTheDocument();
});

it("skips files already present when resuming an upload", async () => {
  const dataset = {
    dataset_name: "speech", formatted_name: "custom:session:speech",
    created_at: "2026-01-01T00:00:00Z", session_id: "session", total_files: 1,
    files: [{ filename: "one.flac", original_filename: "one.flac", size: 3, duration: 1, sample_rate: 16000, uploaded_at: "2026-01-01T00:00:00Z" }],
  };
  stubFetch({ "GET /upload/dataset/list": { json: { datasets: [dataset] } } });
  uploadWithProgress.mockResolvedValue({ dataset_name: dataset.formatted_name, uploaded_files: [{ original_filename: "two.m4a" }] });
  const { CustomDatasetManager } = await import("@/components/dataset/CustomDatasetManager");
  renderWithProviders(<CustomDatasetManager />);
  const user = userEvent.setup();
  await user.click(screen.getByRole("button", { name: /Manage Datasets/ }));
  await user.click(screen.getByRole("tab", { name: /Upload Files/ }));
  await user.selectOptions(screen.getByLabelText("Select Dataset"), "speech");
  await user.upload(screen.getByLabelText("Audio Files"), [
    new File(["one"], "one.flac", { type: "audio/flac" }),
    new File(["two"], "two.m4a", { type: "audio/mp4" }),
  ]);
  await user.click(screen.getByRole("button", { name: /^Upload Files$/ }));

  await waitFor(() => expect(uploadWithProgress).toHaveBeenCalledTimes(1));
  const body = uploadWithProgress.mock.calls[0][1] as FormData;
  expect((body.getAll("files") as File[]).map(file => file.name)).toEqual(["two.m4a"]);
});

it("opens the upload form with a newly created dataset selected", async () => {
  let created = false;
  stubFetch({
    "GET /upload/dataset/list": () => ({ json: { datasets: created ? [{
      dataset_name: "speech", formatted_name: "custom:session:speech",
      created_at: "2026-01-01T00:00:00Z", session_id: "session", files: [], total_files: 0,
    }] : [] } }),
    "POST /upload/dataset/create": () => {
      created = true;
      return { status: 201, json: { dataset_name: "custom:session:speech" } };
    },
  });
  const { CustomDatasetManager } = await import("@/components/dataset/CustomDatasetManager");
  renderWithProviders(<CustomDatasetManager />);
  const user = userEvent.setup();
  await user.click(screen.getByRole("button", { name: /Manage Datasets/ }));
  await user.click(screen.getByRole("tab", { name: /Create Dataset/ }));
  await user.type(screen.getByLabelText("Dataset Name"), "speech");
  await user.click(screen.getByRole("button", { name: /^Create Dataset$/ }));

  await waitFor(() => expect(screen.getByRole("tab", { name: /Upload Files/ })).toHaveAttribute("aria-selected", "true"));
  expect(screen.getByLabelText("Select Dataset")).toHaveValue("speech");
});

it("does not request an unprovisioned built-in dataset on opening J-Lens", async () => {
  const api = stubFetch({
    "GET /models": { json: [] },
    "GET /models/jacobian-lenses/whisper-base": { json: [] },
    "GET /upload/dataset/list": { json: { datasets: [] } },
    "GET /datasets/available": { json: { datasets: [] } },
  });
  const { default: JacobianLensLab } = await import("@/pages/JacobianLensLab");
  renderWithProviders(<JacobianLensLab />, { route: "/jacobian-lens" });

  expect(await screen.findByText(/No training dataset is available/)).toBeInTheDocument();
  expect(api.callsFor("GET /common-voice/metadata")).toHaveLength(0);
  expect(screen.queryByText(/Could not load dataset metadata/)).not.toBeInTheDocument();
});

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
    "GET /datasets/available": { json: { datasets: [] } },
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
  await waitFor(() => expect(api.callsFor(`GET ${metadataPath}`)).toHaveLength(1));
  expect(api.callsFor("GET /common-voice/metadata")).toHaveLength(0);
  const user = userEvent.setup();
  await user.click(screen.getByRole("button", { name: /Manage Datasets/ }));
  await user.click(screen.getByRole("tab", { name: /Upload Files/ }));
  await user.selectOptions(screen.getByLabelText("Select Dataset"), "speech");
  await user.upload(screen.getByLabelText("Audio Files"), [
    new File(["one"], "one.wav", { type: "audio/wav" }),
    new File(["two"], "two.wav", { type: "audio/wav" }),
  ]);
  await user.click(screen.getByRole("button", { name: /^Upload Files$/ }));
  await waitFor(() => expect(api.callsFor(`GET ${metadataPath}`)).toHaveLength(2));
  expect(screen.getByText(/0 transcript-bearing samples available/)).toBeInTheDocument();

  await user.upload(screen.getByLabelText(/Transcript Manifest/),
    new File(["filename,transcript\none.wav,one\ntwo.wav,two\n"], "metadata.csv", { type: "text/csv" }));
  await user.click(screen.getByRole("button", { name: /Upload metadata.csv/ }));
  await waitFor(() => expect(api.callsFor(`GET ${metadataPath}`)).toHaveLength(3));
  expect(await screen.findByText(/2 transcript-bearing samples available/)).toBeInTheDocument();
  await user.click(screen.getAllByRole("button", { name: "Close" })[0]);
  await user.click(screen.getByRole("button", { name: /Select samples/ }));
  expect(screen.getByRole("button", { name: /Fit with 2 samples/ })).toBeEnabled();
});
