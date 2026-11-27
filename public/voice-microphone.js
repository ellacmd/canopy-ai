class CanopyMicrophone extends AudioWorkletProcessor {
  constructor() {
    super();
    this.phase = 0;
    this.sum = 0;
    this.count = 0;
    this.index = 0;
    this.frame = new DataView(new ArrayBuffer(640));
  }

  process(inputs) {
    const channel = inputs[0]?.[0];
    if (!channel) return true;
    for (const sample of channel) {
      this.sum += sample;
      this.count += 1;
      this.phase += 16000;
      if (this.phase < sampleRate) continue;

      this.phase -= sampleRate;
      const value = Math.max(-1, Math.min(1, this.sum / this.count));
      this.frame.setInt16(this.index * 2, value * (value < 0 ? 32768 : 32767), true);
      this.sum = 0;
      this.count = 0;
      this.index += 1;
      if (this.index === 320) {
        this.port.postMessage(
          this.frame.buffer,
          [this.frame.buffer],
        );
        this.frame = new DataView(new ArrayBuffer(640));
        this.index = 0;
      }
    }
    return true;
  }
}

registerProcessor("canopy-microphone", CanopyMicrophone);
