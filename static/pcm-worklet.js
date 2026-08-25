class VoiceGuardPcmProcessor extends AudioWorkletProcessor {
    constructor(options) {
        super();
        this.targetSampleRate = options.processorOptions?.targetSampleRate || 16000;
        this.ratio = sampleRate / this.targetSampleRate;
        this.inputBuffer = new Float32Array(0);
        this.position = 0;
        this.outputBuffer = new Int16Array(1600);
        this.outputOffset = 0;
    }

    appendInput(input) {
        const combined = new Float32Array(this.inputBuffer.length + input.length);
        combined.set(this.inputBuffer);
        combined.set(input, this.inputBuffer.length);
        this.inputBuffer = combined;
    }

    flushOutput() {
        const samples = this.outputBuffer;
        this.port.postMessage({ type: "pcm", samples }, [samples.buffer]);
        this.outputBuffer = new Int16Array(1600);
        this.outputOffset = 0;
    }

    resample() {
        while (this.position + 1 < this.inputBuffer.length) {
            const left = Math.floor(this.position);
            const fraction = this.position - left;
            const interpolated = (
                this.inputBuffer[left] * (1 - fraction)
                + this.inputBuffer[left + 1] * fraction
            );
            const sample = Math.max(-1, Math.min(1, interpolated));
            this.outputBuffer[this.outputOffset] = sample < 0
                ? sample * 32768
                : sample * 32767;
            this.outputOffset += 1;
            this.position += this.ratio;

            if (this.outputOffset === this.outputBuffer.length) {
                this.flushOutput();
            }
        }

        const consumed = Math.floor(this.position);
        if (consumed > 0) {
            this.inputBuffer = this.inputBuffer.slice(consumed);
            this.position -= consumed;
        }
    }

    process(inputs, outputs) {
        const input = inputs[0]?.[0];
        const output = outputs[0]?.[0];
        if (output) output.fill(0);
        if (input?.length) {
            this.appendInput(input);
            this.resample();
        }
        return true;
    }
}

registerProcessor("voiceguard-pcm-processor", VoiceGuardPcmProcessor);
