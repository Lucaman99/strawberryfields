"""
Unit tests for the :mod:`strawberryfields` full toolchain.
"""

import unittest
import inspect
import itertools

from numpy.random import (randn, uniform, randint)
from numpy import array
from numpy import pi

import tensorflow as tf

# NOTE: strawberryfields must be imported from defaults
from defaults import BaseTest, FockBaseTest, GaussianBaseTest, strawberryfields as sf
from strawberryfields.engine import *
from strawberryfields.ops import *
from strawberryfields.backends import BaseFock



class BasicTests(BaseTest):
    """Implementation-independent tests."""
    num_subsystems = 2
    def setUp(self):
        super().setUp()
        # construct a compiler engine
        self.eng = Engine(num_subsystems=self.num_subsystems, hbar=self.hbar)
        # attach the backend (NOTE: self.backend is shared between the tests, make sure it is reset before use!)
        self.eng.backend = self.backend
        self.backend.reset()


    def test_engine(self):
        "Basic engine tests."

        # backed is initialized to vacuum state
        self.assertAllTrue(self.backend.is_vacuum(self.tol))

        D = Dgate(0.5)
        #D = Sgate(0.543)
        with self.eng:
            # trying to act on a nonexistent mode
            self.assertRaises(SFRegRefError, D.__or__, self.eng.num_subsystems)

        # successful gate application
        with self.eng:
            D | 0
        self.assertEqual(len(self.eng.cmd_queue), 1)

        # print the queue contents
        self.eng.print_queue()

        # clear the command queue
        self.eng.reset_queue()
        self.assertEqual(len(self.eng.cmd_queue), 0)

        # fill it again
        with self.eng:
            D | 0

        # run engine
        self.eng.run()
        self.assertEqual(len(self.eng.cmd_queue), 0)
        self.assertEqual(len(self.eng.cmd_applied), 1)
        self.assertEqual(len(self.eng.cmd_applied[0]), 1)

        # see what the state looks like
        temp = self.backend.state()

        # reset the backend state to vacuum
        self.eng.reset()
        self.assertAllTrue(self.backend.is_vacuum(self.tol))


    def test_gate_dagger(self):
        "Dagger functionality of the gates."

        def test_gate(G):
            self.eng.reset()
            q = self.eng.register
            with self.eng:
                if G.ns == 1:
                    G   | q[0]
                    G.H | q[0]
                else:
                    G   | (q[0],q[1])
                    G.H | (q[0],q[1])
            state = self.eng.run()
            # state norm must be invariant
            if isinstance(self.eng.backend, BaseFock):
                self.assertAllAlmostEqual(state.trace(), 1, delta=self.tol,
                                       msg="Trace after G.H * G is not 1 for gate G={}".format(G))
            # we must end up back in vacuum since G and G.H cancel each other
            self.assertAllTrue(self.backend.is_vacuum(self.tol),
                                   msg="State is not vacuum after G.H * G for gate G={}".format(G))

        for G in two_args_gates:
            # construct a random gate
            G = G(uniform(high=0.1), uniform(high=2 * pi))
            test_gate(G)

        for G in one_args_gates:
            if G in (Vgate, Kgate) and not self.args.fock_support:
                continue  # the V gate cannot be used on Gaussian backends
            # construct a random gate
            G = G(uniform(high=0.25))
            test_gate(G)


    def test_regrefs(self):
        """Testing register references."""
        q = self.eng.register
        # using a measurement result before it exists
        with self.eng:
            Dgate(q[0]) | q[1]
        self.assertRaises(SFProgramError, self.eng.run)

        self.eng.reset()
        # proper use
        with self.eng:
            MeasureX | q[0]
            Sgate(q[0])   | q[1]
            Dgate(q[0]).H | q[1]  # symbolic hermitian conjugate together with register reference
            Sgate(RR(q[0], lambda x: x**2)) | q[1]
            Dgate(RR(q[0], lambda x: -x)).H | q[1]
        self.eng.run()


    def test_homodyne_measurement(self):
        """Homodyne measurements."""
        q = self.eng.register
        with self.eng:
            Coherent(*randn(2)) | q[0]
            Coherent(*randn(2)) | q[1]
            MeasureX | q[0]
            MeasureP | q[1]
        self.eng.run()
        # homodyne measurements leave the mode in vacuum state
        self.assertAllTrue(self.backend.is_vacuum(self.tol))

        with self.eng:
            Coherent(*randn(2)) | q[0]
            MeasureHomodyne(*randn(1)) | q[0]
        self.eng.run()
        self.assertAllTrue(self.backend.is_vacuum(self.tol))


    def test_program_subroutine(self):
        """Simple quantum program with a subroutine and references."""
        # define some gates
        D = Dgate(0.5)
        BS = BSgate(0.7*pi, pi/2)
        R = Rgate(pi/3)
        # get register references
        alice, bob = self.eng.register

        def subroutine(a, b):
            "Subroutine for the quantum program"
            R   | a
            BS  | (a,b)
            R.H | a

        # main program
        with self.eng:
            All(Vacuum()) | (alice, bob)
            D   | alice
            subroutine(alice, bob)
            BS  | (alice, bob)
            subroutine(bob, alice)

        state = self.eng.run()
        # state norm must be invariant
        if isinstance(self.eng.backend, BaseFock):
            self.assertAllAlmostEqual(state.trace(), 1, delta=self.tol)


    def _check_reg(self, expected_n=None):
        """Compare Engine.register with the mode list returned by the backend.

        They should always be in agreement after Engine.run(), Engine.reset_queue() and Engine.reset().
        """
        rr = self.eng.register
        modes = self.eng.backend.get_modes()
        # number of elements
        self.assertEqual(len(rr), len(modes))
        if expected_n is not None:
            self.assertEqual(len(rr), expected_n)
        # indices match
        self.assertAllEqual([r.ind for r in rr], modes)
        # activity
        self.assertAllTrue([r.active for r in rr])


    def test_create_delete(self):
        """Creating and deleting modes."""
        # define some gates
        D = Dgate(0.5)
        BS = BSgate(2*pi, pi/2)
        R = Rgate(pi)

        # get register references
        reg = self.eng.register
        alice, bob = reg
        with self.eng:
            D        | alice
            BS       | (alice, bob)
            Del      | alice
            R        | bob
            charlie, = New(1)
            BS       | (bob, charlie)
            MeasureX | bob
            Del      | bob
            D.H      | charlie
            MeasureX | charlie
            # trying to act on a deleted mode
            self.assertRaises(SFRegRefError, D.__or__, alice)
            self.assertRaises(SFRegRefError, D.__or__, bob)

        #self.eng.print_queue()
        self.eng.optimize()
        state = self.eng.run()

        # state norm must be invariant
        if isinstance(self.eng.backend, BaseFock):
            self.assertAllAlmostEqual(state.trace(), 1, delta=self.tol)

        ## check that reset_queue() restores the latest RegRef checkpoint (created by eng.run() above)
        self.assertTrue(not alice.active)
        self.assertTrue(charlie.active)
        self.eng.reset_queue()
        self.assertTrue(not alice.active)
        self.assertTrue(charlie.active)
        with self.eng:
            diana, = New(1)
            Del | charlie
        self.assertTrue(not charlie.active)
        self.assertTrue(diana.active)
        self.eng.reset_queue()
        self.assertTrue(charlie.active)
        self.assertTrue(not diana.active)

        ## check that reset() works
        self._check_reg(1)
        self.eng.reset()
        new_reg = self.eng.register
        # original number of modes
        self.assertEqual(len(new_reg), len(reg))
        # the regrefs are reset as well
        self.assertAllTrue([r.val is None for r in new_reg])
        self._check_reg(2)


    def test_create_delete_reset(self):
        """Test various use cases creating and deleting modes, together with backend resets."""

        # define some gates
        X = Xgate(0.5)

        def prog1(q):
            X        | q
            MeasureX | q

        def prog2(q):
            X        | q
            MeasureX | q
            Del      | q

        reg = self.eng.register
        alice, bob = reg
        eng = self.eng

        ## (1) run several independent programs, resetting everything in between
        with eng:
            prog1(alice)
        self.assertTrue(alice.active)
        self.assertTrue(bob.active)
        eng.run()
        self.assertTrue(alice.val is not None)
        self.assertTrue(bob.val is None)
        eng.reset()
        with eng:
            prog2(bob)
        self.assertTrue(alice.active)
        self.assertTrue(not bob.active)
        eng.run()
        self.assertTrue(alice.val is None)
        self.assertTrue(bob.val is not None)
        eng.reset()

        ## (2) interactive state evolution in multiple runs
        with eng:
            prog2(alice)
        eng.run()
        with eng:
            prog1(bob)
        self.assertTrue(not alice.active)
        self.assertTrue(bob.active)
        eng.run()
        self.assertTrue(alice.val is not None)
        self.assertTrue(bob.val is not None)
        eng.reset()

        ## (3) repeat a (possibly extended) program fragment (the fragment must not create/delete subsystems!)
        with eng:
            prog1(alice)
        eng.run()
        self.assertTrue(alice.val is not None)
        self.assertTrue(bob.val is None)
        eng.cmd_queue.extend(eng.cmd_applied[-1])
        with eng:
            prog1(bob)
        eng.run()
        # both have now been measured
        self.assertTrue(alice.val is not None)
        self.assertTrue(bob.val is not None)
        eng.reset()

        ## (4) interactive use, "changed my mind"
        with eng:
            prog1(alice)
        eng.run()
        with eng:
            prog2(bob)
        self.assertTrue(alice.active)
        self.assertTrue(not bob.active)
        eng.reset_queue()  # scratch that, back to last checkpoint
        self.assertTrue(alice.active)
        self.assertTrue(bob.active)
        self.assertTrue(alice.val is not None)
        self.assertTrue(bob.val is None)
        with eng:
            prog1(bob)
        self.assertTrue(alice.active)
        self.assertTrue(bob.active)
        eng.run()
        self.assertTrue(alice.val is not None)
        self.assertTrue(bob.val is not None)
        eng.reset()

        ## (5) reset the state, run the same program again to get new measurement samples
        with eng:
            prog1(alice)
        eng.run()
        with eng:
            prog2(bob)
        eng.reset(keep_prog=True)
        self.assertTrue(alice.active)
        self.assertTrue(not bob.active)
        eng.run()
        self.assertTrue(alice.active)
        self.assertTrue(not bob.active)


    def test_parameters(self):
        """Test using different types of Parameters with different classes of ParOperations."""
        @sf.convert
        def func1(x):
            return abs(2*x**2 -3*x +1)

        @sf.convert
        def func2(x,y):
            return abs(2*x*y -y**2 +3)

        r = self.eng.register

        # RegRefTransforms for deferred measurements (note that some operations expect nonnegative parameter values)
        rr_inputs = [RR(r[0], lambda x: x**2), func1(r[0]), func2(*r)]
        rr_pars = tuple(Parameter(k) for k in rr_inputs)

        # other types of parameters
        other_inputs = [0.14]  # -4.2+0.5j
        if isinstance(self.backend, sf.backends.TFBackend):
            # add some TensorFlow-specific parameter types
            other_inputs.append(tf.Variable(0.8))
            if self.bsize > 1:
                # test batched input
                other_inputs.append(uniform(size=(self.bsize,)))
        other_pars = tuple(Parameter(k) for k in other_inputs)

        def check(G, par, measure=False):
            "Check a ParOperation/Parameters combination"
            # construct the op using the given tuple of Parameters as args
            G = G(*par)
            with self.eng:
                if measure:
                    # RR parameters require measurements, postselection is much faster
                    MeasureHomodyne(0, select=0.1)     | r[0]
                    MeasureHomodyne(pi/2, select=0.2)  | r[1]
                    #MeasureX  | r[0]
                    #MeasureP  | r[1]
                if G.ns == 1:
                    G | r[0]
                else:
                    G | (r[0], r[1])
            print(G)
            self.eng.optimize()
            try:
                self.eng.run()
            except SFNotApplicableError as err:
                # catch unapplicable op/backend combinations here
                print(err)
                self.eng.reset_queue()  # unsuccessful run means the queue was not emptied.

        scalar_arg_preparations = (Coherent, Squeezed, DisplacedSqueezed, Thermal, Catstate)  # Fock requires an integer parameter
        testset = one_args_gates +two_args_gates +channels +scalar_arg_preparations

        for G in testset:
            sig = inspect.signature(G.__init__)
            n_args = len(sig.parameters) -1  # number of parameters __init__ takes, minus self
            if n_args < 1:
                print('Unexpected number of args ({}) for {}, check the testset.'.format(n_args, G))

            #n_args = min(n_args, 2)   # shortcut, only test cartesian products up to two parameter types
            # check all combinations of Parameter types
            for p in itertools.product(rr_pars+other_pars, repeat=n_args):
                check(G, p, measure=True)



class FockBasisTests(FockBaseTest):
    """Fock-basis dependent tests."""
    num_subsystems = 2
    def setUp(self):
        super().setUp()
        # construct a compiler engine
        self.eng = Engine(num_subsystems=self.num_subsystems, hbar=self.hbar)
        # attach the backend (NOTE: self.backend is shared between the tests, make sure it is reset)
        self.eng.backend = self.backend
        self.backend.reset()

    def test_fock_measurement(self):
        """Fock measurements."""
        q = self.eng.register
        s = randint(0, self.D, (2,))
        with self.eng:
            Fock(s[0]) | q[0]
            Fock(s[1]) | q[1]
            Measure | q
        self.eng.run()
        # measurement result must be equal to preparation
        for a,b in zip(q, s):
            self.assertAllEqual(a.val, b)
        # Fock measurements put the modes into vacuum state
        self.assertAllTrue(self.backend.is_vacuum(self.tol))



class GaussianTests(GaussianBaseTest):
    """Gaussian-only tests."""
    num_subsystems = 2
    def setUp(self):
        super().setUp()
        # construct a compiler engine
        self.eng = Engine(num_subsystems=self.num_subsystems, hbar=self.hbar)
        # attach the backend (NOTE: self.backend is shared between the tests, make sure it is reset)
        self.eng.backend = self.backend
        self.backend.reset()

    def test_gaussian_measurement(self):
        """Gaussian-only measurements."""
        q = self.eng.register
        with self.eng:
            Coherent(*randn(2)) | q[0]
            Coherent(*randn(2)) | q[1]
            MeasureHD | q[0]
            MeasureHD | q[1]
        self.eng.run()
        # heterodyne measurements leave the mode in vacuum state
        self.assertAllTrue(self.backend.is_vacuum(self.tol))


if __name__ == '__main__':
    print('Testing Strawberry Fields version ' + sf.version() + ', full toolchain.')

    # run the tests in this file
    suite = unittest.TestSuite()
    for t in (BasicTests, FockBasisTests, GaussianTests):
        ttt = unittest.TestLoader().loadTestsFromTestCase(t)
        suite.addTests(ttt)

    unittest.TextTestRunner().run(suite)
