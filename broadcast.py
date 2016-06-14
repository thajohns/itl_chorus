import socket
import sys
import struct
import time
import xml.etree.ElementTree as ET
import threading
import thread
import optparse
import random
import itertools

from packet import Packet, CMD, itos

parser = optparse.OptionParser()
parser.add_option('-t', '--test', dest='test', action='store_true', help='Play a test tone (440, 880) on all clients in sequence (the last overlaps with the first of the next)')
parser.add_option('--test-delay', dest='test_delay', type='float', help='Time for which to play a test tone')
parser.add_option('-T', '--transpose', dest='transpose', type='int', help='Transpose by a set amount of semitones (positive or negative)')
parser.add_option('--sync-test', dest='sync_test', action='store_true', help='Don\'t wait for clients to play tones properly--have them all test tone at the same time')
parser.add_option('-R', '--random', dest='random', type='float', help='Generate random notes at approximately this period')
parser.add_option('--rand-low', dest='rand_low', type='int', help='Low frequency to randomly sample')
parser.add_option('--rand-high', dest='rand_high', type='int', help='High frequency to randomly sample')
parser.add_option('-l', '--live', dest='live', help='Enter live mode (play from a controller in real time), specifying the port to connect to as "client,port"; use just "," to manually subscribe later')
parser.add_option('-L', '--list-live', dest='list_live', action='store_true', help='List all the clients and ports that can be connected to for live performance')
parser.add_option('--no-sustain', dest='no_sustain', action='store_true', help='Don\'t use sustain hacks in live mode')
parser.add_option('-q', '--quit', dest='quit', action='store_true', help='Instruct all clients to quit')
parser.add_option('-p', '--play', dest='play', action='append', help='Play a single tone or chord (specified multiple times) on all listening clients (either "midi pitch" or "@frequency")')
parser.add_option('-P', '--play-async', dest='play_async', action='store_true', help='Don\'t wait for the tone to finish using the local clock')
parser.add_option('-D', '--duration', dest='duration', type='float', help='How long to play this note for')
parser.add_option('-V', '--volume', dest='volume', type='float', help='Master volume [0.0, 1.0]')
parser.add_option('-s', '--silence', dest='silence', action='store_true', help='Instruct all clients to stop playing any active tones')
parser.add_option('-S', '--seek', dest='seek', type='float', help='Start time in seconds (scaled by --factor)')
parser.add_option('-f', '--factor', dest='factor', type='float', help='Rescale time by this factor (0<f<1 are faster; 0.5 is twice the speed, 2 is half)')
parser.add_option('-r', '--route', dest='routes', action='append', help='Add a routing directive (see --route-help)')
parser.add_option('-v', '--verbose', dest='verbose', action='store_true', help='Be verbose; dump events and actual time (can slow down performance!)')
parser.add_option('-W', '--wait-time', dest='wait_time', type='float', help='How long to wait for clients to initially respond (delays all broadcasts)')
parser.add_option('-B', '--bind-addr', dest='bind_addr', help='The IP address (or IP:port) to bind to (influences the network to send to)')
parser.add_option('--repeat', dest='repeat', action='store_true', help='Repeat the file playlist indefinitely')
parser.add_option('-n', '--number', dest='number', type='int', help='Number of clients to use; if negative (default -1), use the product of stream count and the absolute value of this parameter')
parser.add_option('-G', '--gui', dest='gui', default='', help='set a GUI to use')
parser.add_option('--pg-fullscreen', dest='fullscreen', action='store_true', help='Use a full-screen video mode')
parser.add_option('--pg-width', dest='pg_width', type='int', help='Width of the pygame window')
parser.add_option('--pg-height', dest='pg_height', type='int', help='Width of the pygame window')
parser.add_option('--help-routes', dest='help_routes', action='store_true', help='Show help about routing directives')
parser.set_defaults(routes=[], test_delay=0.25, random=0.0, rand_low=80, rand_high=2000, live=None, factor=1.0, duration=1.0, volume=1.0, wait_time=0.25, play=[], transpose=0, seek=0.0, bind_addr='', pg_width = 0, pg_height = 0, number=-1)
options, args = parser.parse_args()

if options.help_routes:
    print '''Routes are a way of either exclusively or mutually binding certain streams to certain playback clients. They are especially fitting in heterogenous environments where some clients will outperform others in certain pitches or with certain parts.

Routes are fully specified by:
-The attribute to be routed on (either type "T", or UID "U")
-The value of that attribute
-The exclusivity of that route ("+" for inclusive, "-" for exclusive)
-The stream group to be routed there.

The syntax for that specification resembles the following:

    broadcast.py -r U:bass=+bass -r U:treble1,U:treble2=+treble -r T:BEEP=-beeps,-trk3,-trk5 -r U:noise=0

The specifier consists of a comma-separated list of attribute-colon-value pairs, followed by an equal sign. After this is a comma-separated list of exclusivities paired with the name of a stream group as specified in the file. The above example shows that stream groups "bass", "treble", and "beeps" will be routed to clients with UID "bass", "treble", and TYPE "BEEP" respectively. Additionally, TYPE "BEEP" will receive tracks 4 and 6 (indices 3 and 5) of the MIDI file (presumably split with -T), and that these three groups are exclusively to be routed to TYPE "BEEP" clients only (the broadcaster will drop the stream if no more are available), as opposed to the preference of the bass and treble groups, which may be routed onto other stream clients if they are available. Finally, the last route says that all "noise" UID clients should not proceed any further (receiving "null" streams) instead. Order is important; if a "noise" client already received a stream (such as "+beeps"), then it would receive that route with priority.'''
    exit()

GUIS = {}

def gui_pygame():
    print 'Starting pygame GUI...'
    import pygame, colorsys
    pygame.init()
    print 'Pygame init'

    dispinfo = pygame.display.Info()
    DISP_WIDTH = 640
    DISP_HEIGHT = 480
    if dispinfo.current_h > 0 and dispinfo.current_w > 0:
        DISP_WIDTH = dispinfo.current_w
        DISP_HEIGHT = dispinfo.current_h
    print 'Pygame info'

    WIDTH = DISP_WIDTH
    if options.pg_width > 0:
        WIDTH = options.pg_width
    HEIGHT = DISP_HEIGHT
    if options.pg_height > 0:
        HEIGHT = options.pg_height

    flags = 0
    if options.fullscreen:
        flags |= pygame.FULLSCREEN

    disp = pygame.display.set_mode((WIDTH, HEIGHT), flags)
    print 'Disp acquire'

    PFAC = HEIGHT / 128.0

    clock = pygame.time.Clock()

    print 'Pygame GUI initialized, running...'

    while True:

        disp.scroll(-1, 0)
        disp.fill((0, 0, 0), (WIDTH - 1, 0, 1, HEIGHT))
        idx = 0
        for cli, note in sorted(playing_notes.items(), key = lambda pair: pair[0]):
            pitch = note[0]
            col = colorsys.hls_to_rgb(float(idx) / len(clients), note[1]/2.0, 1.0)
            col = [int(i*255) for i in col]
            disp.fill(col, (WIDTH - 1, HEIGHT - pitch * PFAC - PFAC, 1, PFAC))
            idx += 1
        pygame.display.flip()

        for ev in pygame.event.get():
            if ev.type == pygame.KEYDOWN:
                if ev.key == pygame.K_ESCAPE:
                    thread.interrupt_main()
                    pygame.quit()
                    exit()

        clock.tick(60)

GUIS['pygame'] = gui_pygame

PORT = 13676
factor = options.factor

print 'Factor:', factor

s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
if options.bind_addr:
    addr, _, port = options.bind_addr.partition(':')
    if not port:
        port = '12074'
    s.bind((addr, int(port)))

clients = []
uid_groups = {}
type_groups = {}

s.sendto(str(Packet(CMD.PING)), ('255.255.255.255', PORT))
s.settimeout(options.wait_time)

try:
	while True:
		data, src = s.recvfrom(4096)
		clients.append(src)
except socket.timeout:
	pass

playing_notes = {}
for cli in clients:
    playing_notes[cli] = (0, 0)

print len(clients), 'detected clients'

print 'Clients:'
for cl in clients:
	print cl,
        s.sendto(str(Packet(CMD.CAPS)), cl)
        data, _ = s.recvfrom(4096)
        pkt = Packet.FromStr(data)
        print 'ports', pkt.data[0],
        tp = itos(pkt.data[1])
        print 'type', tp,
        uid = ''.join([itos(i) for i in pkt.data[2:]]).rstrip('\x00')
        print 'uid', uid
        if uid == '':
            uid = None
        uid_groups.setdefault(uid, []).append(cl)
        type_groups.setdefault(tp, []).append(cl)
	if options.test:
                ts, tms = int(options.test_delay), int(options.test_delay * 1000000) % 1000000
		s.sendto(str(Packet(CMD.PLAY, ts, tms, 440, options.volume)), cl)
                if not options.sync_test:
                    time.sleep(options.test_delay)
                    s.sendto(str(Packet(CMD.PLAY, ts, tms, 880, options.volume)), cl)
	if options.quit:
		s.sendto(str(Packet(CMD.QUIT)), cl)
        if options.silence:
                s.sendto(str(Packet(CMD.PLAY, 0, 1, 1, 0.0)), cl)

if options.gui:
    gui_thr = threading.Thread(target=GUIS[options.gui], args=())
    gui_thr.setDaemon(True)
    gui_thr.start()

if options.play:
    for i, val in enumerate(options.play):
        if val.startswith('@'):
            options.play[i] = int(val[1:])
        else:
            options.play[i] = int(440.0 * 2**((int(val) - 69)/12.0))
    for i, cl in enumerate(clients):
        s.sendto(str(Packet(CMD.PLAY, int(options.duration), int(1000000*(options.duration-int(options.duration))), options.play[i%len(options.play)], options.volume)), cl)
    if not options.play_async:
        time.sleep(options.duration)
    exit()

if options.test and options.sync_test:
    time.sleep(0.25)
    for cl in clients:
        s.sendto(str(Packet(CMD.PLAY, 0, 250000, 880, 1.0)), cl)

if options.test or options.quit or options.silence:
    print uid_groups
    print type_groups
    exit()

if options.random > 0:
    while True:
        for cl in clients:
            s.sendto(str(Packet(CMD.PLAY, int(options.random), int(1000000*(options.random-int(options.random))), random.randint(options.rand_low, options.rand_high), options.volume)), cl)
        time.sleep(options.random)

if options.live or options.list_live:
    if options.gui:
        print 'Waiting a second for GUI init...'
        time.sleep(3.0)
    import midi
    from midi import sequencer
    S = sequencer.S
    if options.list_live:
        print sequencer.SequencerHardware()
        exit()
    seq = sequencer.SequencerRead(sequencer_resolution=120)
    client_set = set(clients)
    active_set = {} # note (pitch) -> [client]
    deferred_set = set() # pitches held due to sustain
    sustain_status = False
    client, _, port = options.live.partition(',')
    if client or port:
        seq.subscribe_port(client, port)
    seq.start_sequencer()
    if not options.gui:  # FIXME
        seq.set_nonblock(False)
    while True:
        ev = S.event_input(seq.client)
        if ev is None:
            time.sleep(0)
        event = None
        if ev:
            if options.verbose:
                print 'SEQ:', ev
            if ev < 0:
                seq._error(ev)
            if ev.type == S.SND_SEQ_EVENT_NOTEON:
                event = midi.NoteOnEvent(channel = ev.data.note.channel, pitch = ev.data.note.note, velocity = ev.data.note.velocity)
            elif ev.type == S.SND_SEQ_EVENT_NOTEOFF:
                event = midi.NoteOffEvent(channel = ev.data.note.channel, pitch = ev.data.note.note, velocity = ev.data.note.velocity)
            elif ev.type == S.SND_SEQ_EVENT_CONTROLLER:
                event = midi.ControlChangeEvent(channel = ev.data.control.channel, control = ev.data.control.param, value = ev.data.control.value)
            elif ev.type == S.SND_SEQ_EVENT_PGMCHANGE:
                event = midi.ProgramChangeEvent(channel = ev.data.control.channel, value = ev.data.control.value)
            elif ev.type == S.SND_SEQ_EVENT_PITCHBEND:
                event = midi.PitchWheelEvent(channel = ev.data.control.channel, pitch = ev.data.control.value)
            elif options.verbose:
                print 'WARNING: Unparsed event, type %r'%(ev.type,)
                continue
        if event is not None:
            if isinstance(event, midi.NoteOnEvent) and event.velocity == 0:
                event.__class__ = midi.NoteOffEvent
            if options.verbose:
                print 'EVENT:', event
            if isinstance(event, midi.NoteOnEvent):
                if event.pitch in active_set:
                    if sustain_status:
                        deferred_set.discard(event.pitch)
                inactive_set = client_set - set(sum(active_set.values(), []))
                if not inactive_set:
                    print 'WARNING: Out of clients to do note %r; dropped'%(event.pitch,)
                    continue
                cli = sorted(inactive_set)[0]
                s.sendto(str(Packet(CMD.PLAY, 65535, 0, int(440.0 * 2**((event.pitch-69)/12.0)), event.velocity / 127.0)), cli)
                active_set.setdefault(event.pitch, []).append(cli)
                playing_notes[cli] = (event.pitch, event.velocity / 127.0)
                if options.verbose:
                    print 'LIVE:', event.pitch, '+ =>', active_set[event.pitch]
            elif isinstance(event, midi.NoteOffEvent):
                if event.pitch not in active_set or not active_set[event.pitch]:
                    print 'WARNING: Deactivating inactive note %r'%(event.pitch,)
                    continue
                if sustain_status:
                    deferred_set.add(event.pitch)
                    continue
                cli = active_set[event.pitch].pop()
                s.sendto(str(Packet(CMD.PLAY, 0, 1, 1, 0)), cli)
                playing_notes[cli] = (0, 0)
                if options.verbose:
                    print 'LIVE:', event.pitch, '- =>', active_set[event.pitch]
                    if sustain_status:
                        print '...ignored (sustain on)'
            elif isinstance(event, midi.ControlChangeEvent):
                if event.control == 64 and not options.no_sustain:
                    sustain_status = (event.value >= 64)
                    if options.verbose:
                        print 'LIVE: SUSTAIN', ('+' if sustain_status else '-')
                    if not sustain_status:
                        for pitch in deferred_set:
                            if pitch not in active_set or not active_set[pitch]:
                                print 'WARNING: Attempted deferred removal of inactive note %r'%(pitch,)
                                continue
                            for cli in active_set[pitch]:
                                s.sendto(str(Packet(CMD.PLAY, 0, 1, 1, 0)), cli)
                                playing_notes[cli] = (0, 0)
                            del active_set[pitch]
                        deferred_set.clear()

if options.repeat:
    args = itertools.cycle(args)

for fname in args:
    try:
        iv = ET.parse(fname).getroot()
    except IOError:
        import traceback
        traceback.print_exc()
        print fname, ': Bad file'
        continue

    notestreams = iv.findall("./streams/stream[@type='ns']")
    groups = set([ns.get('group') for ns in notestreams if 'group' in ns.keys()])
    number = (len(notestreams) * abs(options.number) if options.number < 0 else options.number)
    print len(notestreams), 'notestreams'
    print len(clients), 'clients'
    print len(groups), 'groups'
    print number, 'clients used (number)'

    class Route(object):
        def __init__(self, fattr, fvalue, group, excl=False):
            if fattr == 'U':
                self.map = uid_groups
            elif fattr == 'T':
                self.map = type_groups
            elif fattr == '0':
                self.map = {}
            else:
                raise ValueError('Not a valid attribute specifier: %r'%(fattr,))
            self.value = fvalue
            if group is not None and group not in groups:
                raise ValueError('Not a present group: %r'%(group,))
            self.group = group
            self.excl = excl
        @classmethod
        def Parse(cls, s):
            fspecs, _, grpspecs = map(lambda x: x.strip(), s.partition('='))
            fpairs = []
            ret = []
            for fspec in [i.strip() for i in fspecs.split(',')]:
                fattr, _, fvalue = map(lambda x: x.strip(), fspec.partition(':'))
                fpairs.append((fattr, fvalue))
            for part in [i.strip() for i in grpspecs.split(',')]:
                for fattr, fvalue in fpairs:
                    if part[0] == '+':
                        ret.append(Route(fattr, fvalue, part[1:], False))
                    elif part[0] == '-':
                        ret.append(Route(fattr, fvalue, part[1:], True))
                    elif part[0] == '0':
                        ret.append(Route(fattr, fvalue, None, True))
                    else:
                        raise ValueError('Not an exclusivity: %r'%(part[0],))
            return ret
        def Apply(self, cli):
            return cli in self.map.get(self.value, [])
        def __repr__(self):
            return '<Route of %r to %s:%s>'%(self.group, ('U' if self.map is uid_groups else 'T'), self.value)

    class RouteSet(object):
        def __init__(self, clis=None):
            if clis is None:
                clis = clients[:]
            self.clients = clis
            self.routes = []
        def Route(self, stream):
            testset = self.clients[:]
            grp = stream.get('group', 'ALL')
            if options.verbose:
                print 'Routing', grp, '...'
            excl = False
            for route in self.routes:
                if route.group == grp:
                    if options.verbose:
                        print '\tMatches route', route
                    excl = excl or route.excl
                    matches = filter(lambda x, route=route: route.Apply(x), testset)
                    if matches:
                        if options.verbose:
                            print '\tUsing client', matches[0]
                        self.clients.remove(matches[0])
                        return matches[0]
                    if options.verbose:
                        print '\tNo matches, moving on...'
                if route.group is None:
                    if options.verbose:
                        print 'Encountered NULL route, removing from search space...'
                    toremove = []
                    for cli in testset:
                        if route.Apply(cli):
                            toremove.append(cli)
                    for cli in toremove:
                        if options.verbose:
                            print '\tRemoving', cli, '...'
                        testset.remove(cli)
            if excl:
                if options.verbose:
                    print '\tExclusively routed, no route matched.'
                return None
            if not testset:
                if options.verbose:
                    print '\tOut of clients, no route matched.'
                return None
            cli = testset[0]
            self.clients.remove(cli)
            if options.verbose:
                print '\tDefault route to', cli
            return cli

    routeset = RouteSet()
    for rspec in options.routes:
        try:
            routeset.routes.extend(Route.Parse(rspec))
        except Exception:
            import traceback
            traceback.print_exc()

    if options.verbose:
        print 'All routes:'
        for route in routeset.routes:
            print route

    class NSThread(threading.Thread):
        def drop_missed(self):
            nsq, cl = self._Thread__args
            cnt = 0
            while nsq and float(nsq[0].get('time'))*factor < time.time() - BASETIME:
                nsq.pop(0)
                cnt += 1
            if options.verbose:
                print self, 'dropped', cnt, 'notes due to miss'
            self._Thread__args = (nsq, cl)
        def wait_for(self, t):
            if t <= 0:
                return
            time.sleep(t)
	def run(self):
		nsq, cl = self._Thread__args
		for note in nsq:
			ttime = float(note.get('time'))
			pitch = int(note.get('pitch')) + options.transpose
			vel = int(note.get('vel'))
			dur = factor*float(note.get('dur'))
			while time.time() - BASETIME < factor*ttime:
				self.wait_for(factor*ttime - (time.time() - BASETIME))
			s.sendto(str(Packet(CMD.PLAY, int(dur), int((dur*1000000)%1000000), int(440.0 * 2**((pitch-69)/12.0)), int(vel*2 * options.volume/255.0))), cl)
                        if options.verbose:
                            print (time.time() - BASETIME), cl, ': PLAY', pitch, dur, vel
			self.wait_for(dur - ((time.time() - BASETIME) - factor*ttime))
    class NSThread(threading.Thread):
            def drop_missed(self):
                nsq, cl = self._Thread__args
                cnt = 0
                while nsq and float(nsq[0].get('time'))*factor < time.time() - BASETIME:
                    nsq.pop(0)
                    cnt += 1
                if options.verbose:
                    print self, 'dropped', cnt, 'notes due to miss'
                self._Thread__args = (nsq, cl)
            def wait_for(self, t):
                if t <= 0:
                    return
                time.sleep(t)
            def run(self):
                    nsq, cls = self._Thread__args
                    for note in nsq:
                            ttime = float(note.get('time'))
                            pitch = float(note.get('pitch')) + options.transpose
                            ampl = float(note.get('ampl', note.get('vel', 127.0) / 127.0))
                            dur = factor*float(note.get('dur'))
                            while time.time() - BASETIME < factor*ttime:
                                    self.wait_for(factor*ttime - (time.time() - BASETIME))
                            for cl in cls:
                                    s.sendto(str(Packet(CMD.PLAY, int(dur), int((dur*1000000)%1000000), int(440.0 * 2**((pitch-69)/12.0)), ampl * options.volume)), cl)
                            if options.verbose:
                                print (time.time() - BASETIME), cl, ': PLAY', pitch, dur, vel
                            playing_notes[cl] = (pitch, ampl)
                            self.wait_for(dur - ((time.time() - BASETIME) - factor*ttime))
                            playing_notes[cl] = (0, 0)
                    if options.verbose:
                        print '% 6.5f'%(time.time() - BASETIME,), cl, ': DONE'

    threads = {}
    nscycle = itertools.cycle(notestreams)
    for idx, ns in zip(xrange(number), nscycle):
        cli = routeset.Route(ns)
        if cli:
            nsq = ns.findall('note')
            if ns in threads:
                threads[ns]._Thread__args[1].add(cli)
            else:
                threads[ns] = NSThread(args=(nsq, set([cli])))

    if options.verbose:
        print 'Playback threads:'
        for thr in threads.values():
            print thr._Thread__args[1]

    BASETIME = time.time() - (options.seek*factor)
    if options.seek > 0:
        for thr in threads.values():
            thr.drop_missed()
    for thr in threads.values():
            thr.start()
    for thr in threads.values():
            thr.join()
    print fname, ': Done!'
