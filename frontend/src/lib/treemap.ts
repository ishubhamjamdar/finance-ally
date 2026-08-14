/**
 * Squarified treemap layout — the geometry behind the portfolio heatmap.
 *
 * Pure arithmetic, deliberately separated from the component that draws it:
 * "does every tile's area match its weight" and "do the tiles tile the box"
 * are properties you can assert about numbers, and cannot assert about a DOM
 * tree of percentage-positioned divs.
 *
 * The algorithm is Bruls, Huizing and van Wijk (2000). Slice-and-dice — the
 * naive alternative — gives correct areas and unreadable shapes: a small
 * position in a wide panel becomes a hairline you cannot label or click.
 * Squarifying keeps aspect ratios near 1 by filling the shorter side of the
 * remaining space one row at a time.
 *
 * Output is in a unit box by default, so the caller can position tiles with
 * percentages and never measure the container. Nothing here reads the DOM.
 */

export interface TreemapTile<T> {
  item: T;
  /** All four in the same units as `width`/`height` passed in — 0–1 by default. */
  x: number;
  y: number;
  width: number;
  height: number;
}

/**
 * Lay `items` out in a `width` × `height` box, each tile's area proportional
 * to `valueOf(item)`.
 *
 * Items with a non-finite or non-positive value get no tile at all rather than
 * a zero-area one: a holding worth nothing is not a rectangle of nothing, and
 * a caller that wants it on screen has to say so somewhere the user can read.
 * Tiles come back largest first.
 */
export function squarify<T>(
  items: readonly T[],
  valueOf: (item: T) => number | null,
  width = 1,
  height = 1,
): TreemapTile<T>[] {
  if (width <= 0 || height <= 0) return [];

  const entries = items
    .map((item) => ({ item, value: valueOf(item) ?? 0 }))
    .filter((entry) => Number.isFinite(entry.value) && entry.value > 0)
    .sort((a, b) => b.value - a.value);

  if (entries.length === 0) return [];

  // Work in areas from here on, so a row's thickness is just its area over the
  // side it runs along.
  const total = entries.reduce((sum, entry) => sum + entry.value, 0);
  const scale = (width * height) / total;
  const areas = entries.map((entry) => entry.value * scale);

  const tiles: TreemapTile<T>[] = [];
  let x = 0;
  let y = 0;
  let free = { width, height };
  let index = 0;

  while (index < areas.length && free.width > 0 && free.height > 0) {
    const side = Math.min(free.width, free.height);

    // Grow the row while it makes the worst tile in it squarer. The moment it
    // stops, this row is as good as it will get and is placed.
    let end = index + 1;
    let best = worstRatio(areas, index, end, side);
    while (end < areas.length) {
      const next = worstRatio(areas, index, end + 1, side);
      if (next > best) break;
      best = next;
      end += 1;
    }

    let rowArea = 0;
    for (let i = index; i < end; i += 1) rowArea += areas[i];

    if (free.width >= free.height) {
      // A column down the left edge of what is left.
      const columnWidth = rowArea / free.height;
      let offset = y;
      for (let i = index; i < end; i += 1) {
        const tileHeight = areas[i] / columnWidth;
        tiles.push({ item: entries[i].item, x, y: offset, width: columnWidth, height: tileHeight });
        offset += tileHeight;
      }
      x += columnWidth;
      free = { width: free.width - columnWidth, height: free.height };
    } else {
      // A strip across the top of what is left.
      const rowHeight = rowArea / free.width;
      let offset = x;
      for (let i = index; i < end; i += 1) {
        const tileWidth = areas[i] / rowHeight;
        tiles.push({ item: entries[i].item, x: offset, y, width: tileWidth, height: rowHeight });
        offset += tileWidth;
      }
      y += rowHeight;
      free = { width: free.width, height: free.height - rowHeight };
    }

    index = end;
  }

  return tiles;
}

/**
 * The worst aspect ratio in `areas[from, to)` laid along a side of `side`.
 *
 * Lower is squarer; 1 is a perfect square. This is the whole decision the
 * algorithm makes, so it is worth being exact about: the row's thickness is
 * `sum / side`, which makes each tile `area / thickness` long, and the ratio
 * of the two is what this maximises over the row.
 */
function worstRatio(areas: readonly number[], from: number, to: number, side: number): number {
  let sum = 0;
  let max = -Infinity;
  let min = Infinity;
  for (let i = from; i < to; i += 1) {
    sum += areas[i];
    if (areas[i] > max) max = areas[i];
    if (areas[i] < min) min = areas[i];
  }

  if (sum <= 0 || side <= 0) return Infinity;

  const squared = sum * sum;
  const sideSquared = side * side;
  return Math.max((sideSquared * max) / squared, squared / (sideSquared * min));
}
