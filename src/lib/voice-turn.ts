type Decision = "duck" | "resume" | "start" | "end";

const WINDOW_SIZE = 10; // Ten Silero scores cover 320 ms at 16 kHz.
const UNFINISHED = /\b(?:and|or|but|because|so|to|for|with|is|was|number)\s*[,.:…-]*$/i;

export class VoiceTurn {
  speaking = false;
  private ducking = false;
  private scores: number[] = [];
  private voicedWindows = 0;
  private silentWindows = 0;

  observe(probability: number, assistantSpeaking: boolean, transcript: string): Decision | undefined {
    if (this.speaking) {
      if (probability >= 0.4) {
        this.voicedWindows += 1;
        this.silentWindows = 0;
      } else {
        this.silentWindows += 1;
      }
      const pause = UNFINISHED.test(transcript) ? 38 : this.voicedWindows >= 24 ? 31 : 21;
      if (this.silentWindows >= pause) {
        this.reset();
        return "end";
      }
      return;
    }

    this.scores.push(probability);
    if (this.scores.length > WINDOW_SIZE) this.scores.shift();
    const threshold = assistantSpeaking ? 0.7 : 0.55;
    const positive = this.scores.filter((score) => score >= threshold).length;
    if (probability >= threshold && positive >= (assistantSpeaking ? 6 : 3)) {
      this.reset();
      this.speaking = true;
      this.voicedWindows = positive;
      return "start";
    }
    // The volume change is reversible while the call is still in the assistant's turn.
    const possible = assistantSpeaking && this.scores.slice(-3).filter((score) => score >= 0.7).length >= 2;
    if (possible !== this.ducking) {
      this.ducking = possible;
      return possible ? "duck" : "resume";
    }
  }

  reset() {
    this.speaking = false;
    this.ducking = false;
    this.scores = [];
    this.voicedWindows = 0;
    this.silentWindows = 0;
  }
}
