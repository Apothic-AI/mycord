// Wrap SrtpSession.decrypt to see whether inbound SRTP auth is failing.
import { SrtpSession, SrtpAuthenticationError } from 'werift';
const orig = SrtpSession.prototype.decrypt;
const tally = { ok: 0, authFail: 0, otherFail: 0, sampleErr: null, sampleOkHex: null };
SrtpSession.prototype.decrypt = function (buf) {
  try {
    const out = orig.call(this, buf);
    tally.ok++;
    if (!tally.sampleOkHex) tally.sampleOkHex = out.subarray(0, 16).toString('hex');
    return out;
  } catch (e) {
    if (e instanceof SrtpAuthenticationError) { tally.authFail++; if (!tally.sampleErr) tally.sampleErr = 'SrtpAuthenticationError: ' + e.message; }
    else { tally.otherFail++; if (!tally.sampleErr) tally.sampleErr = e.constructor.name + ': ' + e.message; }
    throw e;
  }
};
setTimeout(() => { console.log('SRTP decrypt tally:', JSON.stringify(tally, null, 1)); process.exit(0); }, 55000);
export { tally };
