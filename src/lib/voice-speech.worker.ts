import { Silero } from "@ricky0123/vad-web/dist/models/silero";
import * as ort from "onnxruntime-web/wasm";

export type SpeechInput = { audio: ArrayBuffer; epoch: number };
export type SpeechOutput =
  | { type: "ready" | "error" }
  | { type: "score"; epoch: number; probability?: number };

const port = self as unknown as {
  postMessage: (message: SpeechOutput) => void;
  onmessage: ((event: MessageEvent<SpeechInput>) => void) | null;
};

ort.env.wasm.numThreads = 1;
ort.env.wasm.wasmPaths = {
  wasm: new URL("onnxruntime-web/ort-wasm-simd-threaded.wasm", import.meta.url).href,
};
const modelUrl = new URL("@ricky0123/vad-web/dist/silero_vad_v6.onnx", import.meta.url);
const model = Silero.new(ort, async () => {
  const response = await fetch(modelUrl);
  if (!response.ok) throw new Error("Speech detector asset is unavailable");
  return response.arrayBuffer();
});

const frame = new Float32Array(512);
let filled = 0;
let epoch = -1;
let queue = model.then(() => port.postMessage({ type: "ready" })).catch(reportError);
let failed = false;

function reportError() {
  failed = true;
  port.postMessage({ type: "error" });
}

port.onmessage = ({ data }) => {
  queue = queue.then(async () => {
    if (failed) return;
    const detector = await model;
    if (epoch !== data.epoch) {
      epoch = data.epoch;
      filled = 0;
      detector.reset_state();
    }
    let probability: number | undefined;
    for (const sample of new Int16Array(data.audio)) {
      frame[filled++] = sample / 32768;
      if (filled === frame.length) {
        probability = (await detector.process(frame)).isSpeech;
        filled = 0;
      }
    }
    port.postMessage({ type: "score", epoch, probability });
  }).catch(reportError);
};
