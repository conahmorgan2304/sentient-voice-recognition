class SentientPCMRecorder extends AudioWorkletProcessor {
  constructor({ processorOptions: { maxFrames } }) {
    super();
    this.samples = new Float32Array(maxFrames);
    this.frames = 0;
    this.port.onmessage = () => this.finish();
  }

  finish() {
    if (!this.samples) return;
    const result = this.samples.slice(0, this.frames);
    this.samples = null;
    this.port.postMessage(result, [result.buffer]);
  }

  process(inputs) {
    if (!this.samples) return false;
    const channel = inputs[0]?.[0];
    if (!channel) return true;
    const take = Math.min(channel.length, this.samples.length - this.frames);
    this.samples.set(channel.subarray(0, take), this.frames);
    this.frames += take;
    if (this.frames === this.samples.length) this.finish();
    return !!this.samples;
  }
}

registerProcessor("sentient-pcm-recorder", SentientPCMRecorder);
