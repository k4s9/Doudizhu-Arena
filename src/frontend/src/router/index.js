import { createRouter, createWebHistory } from 'vue-router';
import MatchList from '../views/MatchList.vue';
import MatchView from '../views/MatchView.vue';
import ReplayView from '../views/ReplayView.vue';
import CreateMatch from '../views/CreateMatch.vue';
import PlayerManager from '../views/PlayerManager.vue';
import ConfigManager from '../views/ConfigManager.vue';

const routes = [
  { path: '/', component: MatchList },
  { path: '/create', component: CreateMatch },
  { path: '/players', component: PlayerManager },
  { path: '/configs', component: ConfigManager },
  { path: '/match/:id', component: MatchView, props: true },
  { path: '/replay/:matchId', component: ReplayView, props: true },
  { path: '/replay/:matchId/:handNum', component: ReplayView, props: true },
];

export default createRouter({
  history: createWebHistory(),
  routes,
});
