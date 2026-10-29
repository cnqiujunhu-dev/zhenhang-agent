/** A text region is readable only when enough of it is actually on screen.
 * This is local presentation evidence, not proof of a bank operation. */
export type CustomerRegionObservation = {
  intersectionWidth: number;
  intersectionHeight: number;
  elementWidth: number;
  elementHeight: number;
  textLength: number;
  hidden?: boolean;
  inert?: boolean;
  accessBlocked?: boolean;
  opacity?: number;
};

export function readableCustomerRegion(observation: CustomerRegionObservation): boolean {
  const { intersectionWidth, intersectionHeight, elementWidth, elementHeight, textLength } = observation;
  const dimensions = [intersectionWidth, intersectionHeight, elementWidth, elementHeight, textLength];
  const opacity = observation.opacity ?? 1;
  if (observation.hidden || observation.inert || observation.accessBlocked
    || dimensions.some(value => !Number.isFinite(value) || value < 0)
    || elementWidth <= 0 || elementHeight <= 0 || textLength < 1
    || !Number.isFinite(opacity) || opacity < .75 || opacity > 1) return false;
  return intersectionWidth >= Math.min(80, elementWidth * .7)
    && intersectionHeight >= Math.min(32, elementHeight * .5);
}
