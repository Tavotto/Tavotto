import {build} from './build.ts';
// The only global this script defines. Served as /cla-pdf.js; see the README section "Signed PDF copy".
(globalThis as Record<string,unknown>).TavottoClaPdf=Object.freeze({build});
