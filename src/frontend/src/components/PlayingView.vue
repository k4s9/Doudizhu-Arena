<script setup>
/**
 * PlayingView — the playing phase layout for a single table.
 * Landlord at bottom center, upper-left = landlord's previous active player,
 * upper-right = landlord's next active player. Idle seat is hidden.
 *
 * Fixes:
 * - Better spacing and layout
 * - Last thought persists alongside last play for non-current players
 * - Correct idle seat filtering via effective_idle
 */
import { computed } from 'vue';
import LandlordSeat from './LandlordSeat.vue';
import PlayingSeat from './PlayingSeat.vue';
import CenterBoard from './CenterBoard.vue';
import DizhuCards from './DizhuCards.vue';

const props = defineProps({
  tableLabel: { type: String, default: 'A' },
  dealer: { type: String, default: '' },
  landlord: { type: String, default: '' },
  finalBid: { type: Number, default: 0 },
  idleSeat: { type: String, default: '' },
  players: { type: Object, default: () => ({}) },
  currentSeat: { type: String, default: '' },
  currentPattern: { type: Object, default: null },
  playHistory: { type: Array, default: () => [] },
  handCards: { type: Object, default: () => ({}) },
  thoughts: { type: Object, default: () => ({}) },
  dizhuCards: { type: Array, default: () => [] },
  /** Map of seat -> last thought that accompanied their last action */
  lastThoughts: { type: Object, default: () => ({}) },
});

const TURN_CYCLE = ['S', 'E', 'N', 'W'];

/** Active players = TURN_CYCLE minus idle_seat (3 players) */
const activeOrder = computed(() => {
  return TURN_CYCLE.filter(s => s !== props.idleSeat);
});

/** landlord's position in activeOrder */
const lordIdx = computed(() => {
  return activeOrder.value.indexOf(props.landlord);
});

/** Landlord's next active player (clockwise) = upper right */
const lordNext = computed(() => {
  if (lordIdx.value < 0) return '';
  return activeOrder.value[(lordIdx.value + 1) % 3];
});

/** Landlord's previous active player (counter-clockwise) = upper left */
const lordPrev = computed(() => {
  if (lordIdx.value < 0) return '';
  return activeOrder.value[(lordIdx.value + 2) % 3];
});

/** Get last played action for a seat (cleared when it's this seat's turn again) */
function lastPlayedAction(seat) {
  if (props.currentSeat === seat) return null; // clear when it's their turn
  for (let i = props.playHistory.length - 1; i >= 0; i--) {
    const r = props.playHistory[i];
    if (r.seat === seat && r.action?.type === 'play') {
      return r.action;
    }
  }
  return null;
}

/** Get last action record for a seat (cleared when it's this seat's turn again) */
function lastAction(seat) {
  if (props.currentSeat === seat) return null; // clear when it's their turn
  for (let i = props.playHistory.length - 1; i >= 0; i--) {
    const r = props.playHistory[i];
    if (r.seat === seat) {
      return r;
    }
  }
  return null;
}

/** Get the most recent play (for CenterBoard display) */
const lastPlayOverall = computed(() => {
  for (let i = props.playHistory.length - 1; i >= 0; i--) {
    const r = props.playHistory[i];
    if (r.action?.type === 'play') return r;
  }
  return null;
});

/** Get the most recent pass (for CenterBoard display) */
const lastPassOverall = computed(() => {
  for (let i = props.playHistory.length - 1; i >= 0; i--) {
    const r = props.playHistory[i];
    if (r.action?.type === 'pass') return r;
  }
  return null;
});

/** Determine if we're in a new round (center should be clear) */
const isNewRound = computed(() => {
  if (props.currentPattern) return false;
  if (!props.playHistory.length) return true;
  return true;
});

/**
 * Get the thought to display for a seat.
 * If this seat is the current player, show the live thought.
 * Otherwise, show the last persisted thought (that accompanied their last action).
 */
function seatThought(seat) {
  if (props.currentSeat === seat) {
    return props.thoughts[seat] || props.lastThoughts[seat] || null;
  }
  // For non-current players, show their last thought if they just acted
  const la = lastAction(seat);
  if (la) {
    return props.lastThoughts[seat] || null;
  }
  return null;
}
</script>

<template>
  <div class="flex flex-col gap-2">
    <!-- Header -->
    <div class="flex items-center justify-between mb-1">
      <div class="flex items-center gap-2">
        <h3 class="text-base font-semibold text-slate-200">{{ tableLabel }} 桌</h3>
        <span class="text-[10px] px-2 py-0.5 rounded-full bg-green-500/20 text-green-400 font-semibold">出牌中</span>
        <span v-if="finalBid > 0" class="text-[10px] px-2 py-0.5 rounded-full bg-amber-500/20 text-amber-400 font-semibold">叫 {{ finalBid }} 分</span>
      </div>
      <DizhuCards :cards="dizhuCards" size="sm" />
    </div>

    <!-- Playing layout -->
    <div class="flex flex-col gap-2">
      <!-- Upper row: lordPrev (left) + lordNext (right) -->
      <div class="flex justify-between gap-2">
        <PlayingSeat
          v-if="lordPrev"
          :seat="lordPrev"
          :player="players[lordPrev]"
          :is-current-player="currentSeat === lordPrev"
          :last-played="lastPlayedAction(lordPrev)"
          :last-action="lastAction(lordPrev)"
          :hand-cards="handCards[lordPrev] || []"
          :show-hand-cards="true"
          :thought="seatThought(lordPrev)"
        />
        <PlayingSeat
          v-if="lordNext"
          :seat="lordNext"
          :player="players[lordNext]"
          :is-current-player="currentSeat === lordNext"
          :last-played="lastPlayedAction(lordNext)"
          :last-action="lastAction(lordNext)"
          :hand-cards="handCards[lordNext] || []"
          :show-hand-cards="true"
          :thought="seatThought(lordNext)"
        />
      </div>

      <!-- Center info board -->
      <CenterBoard
        :dealer="dealer"
        :landlord="landlord"
        :final-bid="finalBid"
        :current-pattern="currentPattern"
        :last-play="lastPlayOverall"
        :last-pass="lastPassOverall"
        :new-round="isNewRound"
      />

      <!-- Bottom: Landlord (main display) -->
      <LandlordSeat
        v-if="landlord"
        :seat="landlord"
        :player="players[landlord]"
        :is-current-player="currentSeat === landlord"
        :hand-cards="handCards[landlord] || []"
        :last-played="lastPlayedAction(landlord)"
        :last-action="lastAction(landlord)"
        :thought="seatThought(landlord)"
      />
    </div>
  </div>
</template>
