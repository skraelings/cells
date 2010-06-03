#!/usr/bin/env python
#TODO:
# - Make terrain work
# - Make ScalarView
# - Add more actions: PASS, LIFT, DROP,etc...
# - derive SelfView with more info than the general AgentView
# - render terrain and energy landscapes
# - fractal terrain generation
# - make rendering "smart"(and/or openGL)
# - Split into several files.
# - Messaging system
# - limit frame rate
# - response objects for outcome of action
# - Desynchronize agents

import ConfigParser
import itertools
import math
import random
import sys
import time

import numpy
import pygame, pygame.locals


config = ConfigParser.RawConfigParser()


def get_mind(name):
    full_name = 'minds.' + name
    __import__(full_name)
    return sys.modules[full_name]


TIMEOUT = None


def main():
    global bounds, symmetric, mind_list
    
    try:
        config.read('default.cfg')
        bounds = config.getint('terrain', 'bounds')
        symmetric = config.getboolean('terrain', 'symmetric')
        minds_str = str(config.get('minds', 'minds'))
        mind_list = [get_mind(n) for n in minds_str.split(',')]
    except Exception as e:
        print 'Got error: %s' % e
        config.add_section('minds')
        config.set('minds', 'minds', 'mind1,mind2')
        config.add_section('terrain')
        config.set('terrain', 'bounds', '300')
        config.set('terrain', 'symmetric', 'true')

        with open('default.cfg', 'wb') as configfile:
            config.write(configfile)

        config.read('default.cfg')
        bounds = config.getint('terrain', 'bounds')
        symmetric = config.getboolean('terrain', 'symmetric')

    # accept command line arguments for the minds over those in the config
    try:
        if len(sys.argv)>2:
            mind_list = [get_mind(n) for n in sys.argv[1:] ]
    except (ImportError, IndexError):
        pass


try:
    import psyco
    psyco.full()
except ImportError:
    pass
    

def signum(x):
    return cmp(x, 0)


class Game:
    def __init__(self, bounds, mind_list, symmetric, max_time):
        self.size = self.width, self.height = (bounds, bounds)
        self.messages = [MessageQueue() for x in mind_list]
        self.disp = Display(self.size,scale=2)
        self.time = 0
        self.max_time = max_time
        self.tic = time.time()
        self.terr = ScalarMapLayer(self.size)
        self.terr.set_random(5)
        self.minds = [m.AgentMind for m in mind_list]
        self.update_fields = [(x, y) for x in xrange(self.width)
                                     for y in xrange(self.height)]

        self.energy_map = ScalarMapLayer(self.size)
        self.energy_map.set_random(10)

        self.plant_map = ObjectMapLayer(self.size, None)
        self.plant_population = []

        self.agent_map = ObjectMapLayer(self.size, None)
        self.agent_population = []
        self.winner = None
        if symmetric:
            self.n_plants = 7
        else:
            self.n_plants = 14

        for x in xrange(self.n_plants):
            mx = random.randrange(1, self.width - 1)
            my = random.randrange(1, self.height - 1)
            eff = random.randrange(5, 11)
            p = Plant(mx, my, eff)
            self.plant_population.append(p)
            if symmetric:
                p = Plant(my, mx, eff)
                self.plant_population.append(p)
        self.plant_map.insert(self.plant_population)

        for idx in xrange(len(self.minds)):
            (mx, my) = self.plant_population[idx].get_pos()
            fuzzed_x = mx + random.randrange(-1, 2)
            fuzzed_y = my + random.randrange(-1, 2)
            self.agent_population.append(Agent(fuzzed_x, fuzzed_y, idx,
                                               self.minds[idx], None))
            self.agent_map.insert(self.agent_population)

    def run_plants(self):
        for p in self.plant_population:
            (x, y) = p.get_pos()
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    adj_x = x + dx
                    adj_y = y + dy
                    if self.energy_map.in_range(adj_x, adj_y):
                        self.energy_map.change(adj_x, adj_y, p.get_eff())

    def add_agent(self, a):
        self.agent_population.append(a)
        self.agent_map.set(a.x, a.y, a)

    def del_agent(self, a):
        self.agent_population.remove(a)
        self.agent_map.set(a.x, a.y, None)
        a.alive = False

    def move_agent(self, a, x, y):
        if abs(self.terr.get(x, y)-self.terr.get(a.x, a.y)) <= 4:
            self.agent_map.set(a.x, a.y, None)
            self.agent_map.set(x, y, a)
            a.x = x
            a.y = y

    def get_next_move(self, old_x, old_y, x, y):
        dx = signum(x - old_x)
        dy = signum(y - old_y)
        return (old_x + dx, old_y + dy)

    def run_agents(self):
        views = []
        self.update_fields = []
        update_fields_append = self.update_fields.append
        agent_map_get_small_view_fast = self.agent_map.get_small_view_fast
        plant_map_get_small_view_fast = self.plant_map.get_small_view_fast
        energy_map = self.energy_map
        WV = WorldView
        views_append = views.append
        for a in self.agent_population:
            update_fields_append(a.get_pos())
            x = a.x
            y = a.y
            agent_view = agent_map_get_small_view_fast(x, y)
            plant_view = plant_map_get_small_view_fast(x, y)
            world_view = WV(a, agent_view, plant_view, energy_map)
            views_append((a, world_view))

        #get actions
        messages = self.messages
        actions = [(a, a.act(v, messages[a.team])) for (a, v) in views]
        random.shuffle(actions)

        #apply agent actions
        for (agent, action) in actions:
            agent.energy -= 1
#      if agent.alive:
            if action.type == ACT_MOVE:
                act_x, act_y = action.get_data()
                (new_x, new_y) = self.get_next_move(agent.x, agent.y,
                                                    act_x, act_y)
                if (self.agent_map.in_range(new_x, new_y) and
                    not self.agent_map.get(new_x, new_y)):
                    self.move_agent(agent, new_x, new_y)
            elif action.type == ACT_SPAWN:
                act_x, act_y = action.get_data()[:2]
                (new_x, new_y) = self.get_next_move(agent.x, agent.y,
                                                    act_x, act_y)
                if (self.agent_map.in_range(new_x, new_y) and
                    not self.agent_map.get(new_x, new_y) and
                    agent.energy >= 50):
                    a = Agent(new_x, new_y, agent.get_team(),
                              self.minds[agent.get_team()],
                              action.get_data()[2:])
                    self.add_agent(a)
                    agent.energy -= 50
            elif action.type == ACT_EAT:
                intake = self.energy_map.get(agent.x, agent.y)
                agent.energy += intake
                self.energy_map.change(agent.x, agent.y, -intake)
            elif action.type == ACT_ATTACK:
                act_x, act_y = act_data = action.get_data()
                next_pos = self.get_next_move(agent.x, agent.y, act_x, act_y)
                new_x, new_y = next_pos
                victim = self.agent_map.get(act_x, act_y)
                if (victim is not None and next_pos == act_data and
                    victim.alive):
                    energy = self.agent_map.get(new_x, new_y).energy + 25
                    self.energy_map.change(new_x, new_y, energy)
                    self.del_agent(self.agent_map.get(new_x, new_y))
            elif action.type == ACT_LIFT:
                if not agent.loaded and self.terr.get(agent.x, agent.y) > 0:
                    agent.loaded = True
                    self.terr.change(agent.x, agent.y, -1)
            elif action.type == ACT_DROP:
                if agent.loaded:
                    agent.loaded = False
                    self.terr.change(agent.x, agent.y, 1)

        #let agents die if their energy is too low
        team = [0 for n in self.minds]
        for (agent, action) in actions:
            if agent.energy < 0 and agent.alive:
                self.energy_map.change(agent.x, agent.y, 25)
                self.del_agent(agent)
            else :
                team[agent.team] += 1

        for (idx, val) in enumerate(team):
            if val == 0:
                self.minds[idx] = None
        # reduce to [None, <players left>]
        _s = set(self.minds)
        if len(set(self.minds)) <= 2:
            print "Winner is", list(_s)[1], "in " + str(self.time)
            self.winner = True

        if self.max_time > 0 and self.time > self.max_time:
            print "It's a draw!"
            self.winner = -1

    def tick(self):
        self.disp.update(self.terr, self.agent_population,
                         self.plant_population, self.update_fields)
        self.disp.flip()

        # test for spacebar pressed - if yes, restart
        for event in pygame.event.get():
            if (event.type == pygame.locals.KEYUP and
                event.key == pygame.locals.K_SPACE):
                self.winner = -1

        self.run_agents()
        self.run_plants()
        for msg in self.messages:
            msg.update()
        self.time += 1
        if TIMEOUT is not None and self.time > TIMEOUT and not self.winner:
            print 'no winner due to timeout'
            self.winner = True
#pygame.time.wait(int(1000*(time.time()-self.tic)))
        self.tic = time.time()


class MapLayer:
    def __init__(self, size, val=0):
        self.size = self.width, self.height = size
        array_data = [[val for x in xrange(self.width)]
                       for y in xrange(self.height)]
        self.values = numpy.array(array_data, numpy.object_)

    def get(self, x, y):
        if y >= 0 and x >= 0:
            try:
                return self.values[x, y]
            except IndexError:
                return None
        return None

    def set(self, x, y, val):
        self.values[x, y] = val

    def in_range(self, x, y):
        return (0 <= x < self.width and 0 <= y < self.height)


class ScalarMapLayer(MapLayer):
    def set_random(self, range):
        self.values = numpy.random.random_integers(0, range - 1,
                                                   (self.width, self.height))

    def change(self, x, y, val):
        self.values[x, y] += val


class ObjectMapLayer(MapLayer):
    def get_small_view_fast(self, x, y):
        ret = []
        get = self.get
        append = ret.append
        width = self.width
        height = self.height
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                if not (dx or dy):
                    continue
                try:
                    adj_x = x + dx
                    if not 0 <= adj_x < width:
                        continue
                    adj_y = y + dy
                    if not 0 <= adj_y < height:
                        continue
                    a = self.values[adj_x, adj_y]
                    if a is not None:
                        append(a.get_view())
                except IndexError:
                    pass
        return ret

    def get_view(self, x, y, r):
        ret = []
        for x_off in xrange(-r, r + 1):
            for y_off in xrange(-r, r + 1):
                if x_off == 0 and y_off == 0:
                    continue
                a = self.get(x + x_off, y + y_off)
                if a is not None:
                    ret.append(a.get_view())
        return ret

    def insert(self, list):
        for o in list:
            self.set(o.x, o.y, o)
            

class Agent:
    __slots__ = ['x', 'y', 'mind', 'energy', 'alive', 'team', 'loaded', 'color',
                 'act']
    def __init__(self, x, y, team, AgentMind, cargs):
        self.x = x
        self.y = y
        self.mind = AgentMind(cargs)
        self.energy = 25
        self.alive = True
        self.team = team
        self.loaded = False
        colors = [(255, 0, 0), (0, 0, 255), (255, 0, 255), (255, 255, 0)]
        self.color = colors[team % len(colors)]
        self.act = self.mind.act

    def get_team(self):
        return self.team

    def get_pos(self):
        return (self.x, self.y)

    def set_pos(self, x, y):
        self.x = x
        self.y = y

    def get_view(self):
        return AgentView(self)


ACT_SPAWN, ACT_MOVE, ACT_EAT, ACT_ATTACK, ACT_LIFT, ACT_DROP = range(6)


class Action:
    '''
    A class for passing an action around.
    '''
    def __init__(self, action_type, data=None):
        self.type = action_type
        self.data = data

    def get_data(self):
        return self.data

    def get_type(self):
        return self.type


class PlantView:
    def __init__(self, p):
        self.x = p.x
        self.y = p.y
        self.eff = p.get_eff()

    def get_pos(self):
        return (self.x, self.y)

    def get_eff(self):
        return self.eff


class AgentView:
    def __init__(self, agent):
        (self.x, self.y) = agent.get_pos()
        self.team = agent.get_team()

    def get_pos(self):
        return (self.x, self.y)

    def get_team(self):
        return self.team


class WorldView:
    def __init__(self, me, agent_views, plant_views, energy_map):
        self.agent_views = agent_views
        self.plant_views = plant_views
        self.energy_map = energy_map
        self.me = me

    def get_me(self):
        return self.me

    def get_agents(self):
        return self.agent_views

    def get_plants(self):
        return self.plant_views

    def get_energy(self):
        return self.energy_map


class Display:
    black = (0, 0, 0)
    red = (255, 0, 0)
    green = (0, 255, 0)
    yellow = (255, 255, 0)

    def __init__(self, size, scale=5):
        self.width, self.height = size
        self.scale = scale
        self.size = (self.width * scale, self.height * scale)
        pygame.init()
        self.screen = pygame.display.set_mode(self.size)

    def update(self, terr, pop, plants, upfields):
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                sys.exit()

        scale_tup = (self.scale, self.scale)
        for f in upfields:
            (x, y) = f
            scaled_x = x * self.scale
            scaled_y = y * self.scale
            color = (
                min(255, 20 * terr.get(x, y)), 
                min(255, 10 * terr.get(x, y)),
                0)
            self.screen.fill(color, pygame.Rect((scaled_x, scaled_y), scale_tup))
        for a in pop:
            (x, y) = a.get_pos()
            x *= self.scale
            y *= self.scale
            self.screen.fill(a.color, pygame.Rect((x, y), scale_tup))
        for a in plants:
            (x, y) = a.get_pos()
            x *= self.scale
            y *= self.scale
            self.screen.fill(self.green, pygame.Rect((x, y), scale_tup))

    def flip(self):
        pygame.display.flip()


class Plant:
    def __init__(self, x, y, eff):
        self.x = x
        self.y = y
        self.eff = eff

    def get_pos(self):
        return (self.x, self.y)

    def get_eff(self):
        return self.eff

    def get_view(self):
        return PlantView(self)


class MessageQueue:
    def __init__(self):
        self.__inlist = []
        self.__outlist = []

    def update(self):
        self.__outlist = self.__inlist
        self.__inlist = []

    def send_message(self, m):
        self.__inlist.append(m)

    def get_messages(self):
        return self.__outlist


class Message:
    def __init__(self, message):
        self.message = message
    def get_message(self):
        return self.message


if __name__ == "__main__":
    main()
    while True:
        game = Game(bounds, mind_list, symmetric, -1)
        while game.winner == None:
            game.tick()
