export default class Memory {
  constructor() {
    this._seq = 0
    this._data = []
  }

  toString() {
    let r = `${this._data.length} elems:\n`
    r += this._data.map(([off,seq,val]) => `  - ${off},${seq}: ${val.reduce((acc, v) => acc + v.toString(16).padStart(2, '0'), '')} | ${typeof val}`).join('\n')
    return r
  }

  store(offset, value) {
    this._data.push([offset, this._seq, value])
    this._seq += 1
  }

  load(offset) {
    this._data = this._data.sort((a, b) => a[0] - b[0])

    const res = new Array(32).fill([-1, 0, undefined])
    for (let i = offset; i < offset + 32; i++) {
      const idx = i - offset
      for (const [off, seq, val] of this._data) {
        if (seq > res[idx][0] && i >= off && i < off + val.length) {
          res[idx] = [seq, val[i - off], val]
        }
      }
    }
    const ret = new Uint8Array(res.map((v) => v[1]))
    const used = new Set(res.map((v) => v[2]))
    return [ret, used]
  }
}
